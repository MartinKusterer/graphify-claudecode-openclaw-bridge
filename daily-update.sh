#!/bin/bash
# Tägliches Update der Konversations-Markdowns für den Knowledge Graph.
# Konvertiert Sessions → Markdown, dann triggert llm-update.py (OpenRouter free tier)
# für die Semantik-Extraktion. Das Flag NEEDS_GRAPH_UPDATE wird von llm-update.py
# entfernt, wenn alles erfolgreich war — bleibt stehen bei Teil-Fehlern.

set -u
LOG_DIR=/var/log/graphify
LOG_FILE=$LOG_DIR/daily-update.log
BRIDGE=/root/graphify-bridge
CONV_DIR=$BRIDGE/conversations
FLAG=$BRIDGE/NEEDS_GRAPH_UPDATE
STATE=$BRIDGE/.last-hash

mkdir -p "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

log "--- Start daily-update ---"

# Hash VOR dem Konvertieren (für Change-Detection)
BEFORE_HASH=""
if [ -d "$CONV_DIR" ]; then
    BEFORE_HASH=$(find "$CONV_DIR" -type f -name '*.md' -exec sha256sum {} \; 2>/dev/null | sort | sha256sum | awk '{print $1}')
fi

# Konvertieren
if ! "$BRIDGE/venv/bin/python3" "$BRIDGE/convert-conversations.py" >> "$LOG_FILE" 2>&1; then
    log "ERROR: convert-conversations.py fehlgeschlagen"
    exit 1
fi

AFTER_HASH=$(find "$CONV_DIR" -type f -name '*.md' -exec sha256sum {} \; 2>/dev/null | sort | sha256sum | awk '{print $1}')

if [ "$BEFORE_HASH" = "$AFTER_HASH" ]; then
    log "Keine Änderungen."
    # Flag nicht anfassen — bleibt ggf. von einem früheren Lauf
    exit 0
fi

FILE_COUNT=$(find "$CONV_DIR" -type f -name '*.md' | wc -l)
log "Änderungen erkannt. Jetzt $FILE_COUNT Markdown-Dateien."

# Flag setzen (wird von llm-update.py entfernt, wenn erfolgreich)
cat > "$FLAG" <<EOF
# Knowledge Graph Update erforderlich
Erstellt: $(date -Iseconds)
Grund: Neue oder geänderte Konversationen erkannt.
Dateien im Korpus: $FILE_COUNT
EOF

echo "$AFTER_HASH" > "$STATE"
log "Flag gesetzt: $FLAG"

# LLM-gestütztes Graph-Update (OpenRouter free tier, ~3-4 min pro Datei)
log "Starte llm-update.py (kann eine Weile dauern bei vielen neuen Dateien)..."
if "$BRIDGE/venv/bin/python3" "$BRIDGE/llm-update.py" >> "$LOG_FILE" 2>&1; then
    log "llm-update.py erfolgreich."
else
    RC=$?
    log "llm-update.py Exit-Code $RC (Flag bleibt für Retry)."
fi

log "--- Ende daily-update ---"
