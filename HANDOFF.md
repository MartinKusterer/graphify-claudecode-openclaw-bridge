---
author: claude-code
date: 2026-05-12
type: discussion
tags: [handoff, knowledge-graph, vault, graphify, obsidian, cron, github-sync, token-efficiency, claude-md-lean, reference, graphify/manual]
audience: human-and-llm
---

# Knowledge-System Übergabe an Kollegen (2026-05-12)

> Ziel: Diese Datei reicht aus, damit eine andere Person (oder ein anderer Claude) das hier beschriebene Knowledge-System für Claude Code **nachbauen** kann. Geschrieben so, dass sowohl ein Mensch beim Durchlesen als auch eine LLM beim Auto-Parsing direkt loslegen können. Lies sie linear einmal von oben durch — Reihenfolge ist Setup-Reihenfolge.

## 1. Was ist das System (TL;DR)

Eine Persistenz-Schicht für Claude Code, bestehend aus vier eng verzahnten Producer-Komponenten:

1. **Obsidian-Vault** (`/docker/obsidian/vault/`) — flach, eine Markdown-Datei pro Knoten, im Container gemountet und über HTTPS als Web-UI erreichbar.
2. **Graphify-Bridge** (`/root/graphify-bridge/`) — konvertiert Claude-Code-Sessions (JSONL) → Markdown, baut daraus täglich einen Knowledge-Graph (Nodes, Edges, Communities) mit einem **kostenlosen** LLM (OpenRouter free tier).
3. **MCP-Server `graphify`** — stdio-Server, gibt Claude Code Lese-Tools auf den Graph (`get_node`, `get_neighbors`, `query_graph`, `shortest_path`, `god_nodes`, `graph_stats`).
4. **GitHub-Repo-Spiegel** (`/root/jarvis-repo/` → `https://github.com/MartinKusterer/J.A.R.V.I.S.`) — alle Server-Skripte, der Vault und alle Configs werden in einen idempotenten Sync gepusht (mit Secret-Whitelist).

… und zwei **Consumer-Komponenten** auf der OpenClaw-Seite (optional — der Vault funktioniert auch ohne, dann nur für Terminal-Claude), die den Vault als Kommunikations-Brücke nutzen:

5. **Oracle-Agent** (OpenClaw Agent Index 13) — der **Vault-Gatekeeper auf OpenClaw-Seite**. Jarvis ruft ihn via `sessions_spawn oracle` auf, wenn eine User-Anfrage etwas im Knowledge-Graph erfordert. Oracle **liest** Vault-Knoten und kann **neue Knoten zurückschreiben** (Synthesen, Wiki-Verknüpfungen). Damit ist der Vault eine **bidirektionale Brücke**: Terminal-Claude schreibt rein → Oracle/Jarvis lesen → Oracle schreibt Synthesen zurück → nächste Terminal-Session sieht sie.
6. **Jarvis Dreaming-Phase** (nightly Cron 03:00 Berlin im OpenClaw-Gateway) — drei-stufige Memory-Consolidation (Light → REM → Deep). Liest Vault + STM-Signale, verdichtet zu `MEMORY.md` (LTM) und schreibt `DREAMS.md` (human-readable Dream Diary). **Dadurch lernt Jarvis ohne Zutun, was Terminal-Claude tagsüber gemacht hat** — alles was im Vault landet, wird in der nächsten Nacht in Jarvis' Langzeit-Gedächtnis konsolidiert.

**Zentrales Pattern**: `CLAUDE.md` (Boot-Instruktionen) wird in **jeden** Turn geladen und kostet daher pro Zeile dauerhaft Tokens. Operative Details liegen **nicht** in `CLAUDE.md`, sondern in Vault-Knoten, die nur **on-demand** gelesen werden (über `grep` oder den MCP-Server). Das spart messbar Tokens (siehe §3).

**Zweites zentrales Pattern**: Der Vault ist **kein passiver Speicher**, sondern ein Kommunikations-Kanal zwischen zwei LLM-Welten. Terminal-Claude (Opus 4.7) und Jarvis (OpenClaw, schwächere Free-Tier-Modelle) leben getrennt — der Vault ist ihr gemeinsamer Schreibtisch. Was die eine Seite hinlegt, findet die andere am nächsten Tag.

## 2. Architektur-Diagramm

```
                            ┌─────────────────────────────┐
                            │   Claude Code Session       │
                            │   (Terminal, /root)         │
                            └──────────────┬──────────────┘
                                           │
                                Boot-Load: /root/CLAUDE.md  (≤200 Z., lean)
                                On-demand:  grep Vault  /  MCP graphify
                                           │
                ┌──────────────────────────┼──────────────────────────┐
                │                          │                          │
                ▼                          ▼                          ▼
   ┌─────────────────────┐   ┌──────────────────────────┐   ┌──────────────────┐
   │  Memory (MD-Index)  │   │  Vault  /docker/...      │   │  MCP graphify    │
   │  /root/.claude/...  │   │  ~360 .md Knoten         │   │  stdio-Server    │
   │  /memory/MEMORY.md  │   │  flat, YAML-Frontmatter  │   │  liest graph.json│
   └─────────────────────┘   └────────────┬─────────────┘   └─────────┬────────┘
                                          │                           │
                                          │ ◄──── täglich 03:30 ──────┤
                                          │                           │
                              ┌───────────┴───────────┐               │
                              │ /root/graphify-bridge │               │
                              │  daily-update.sh      │               │
                              │   1. convert-... .py  │               │
                              │      JSONL → MD       │               │
                              │   2. llm-update.py    │               │
                              │      MD  → graph.json │ ──────────────┘
                              │      (OpenRouter free)│
                              └───────────────────────┘

                              ┌───────────────────────────────────┐
                              │  /root/jarvis-repo (git)          │
                              │  sync-from-server.sh              │
                              │  rsync mit Whitelist + Secret-Excl│
                              │  → github.com/.../J.A.R.V.I.S.    │
                              │  ◄── Mon 04:00 UTC (Backstop)     │
                              │  ◄── manuell nach jedem Change    │
                              └───────────────────────────────────┘
                                          ▲
                                          │
                          spiegelt: vault/, scripts/, openclaw/,
                                    pixel-office/, jarvis-monitor/,
                                    webclipper/, host-config/crontab
```

### 2.1 Consumer-Side (OpenClaw, optional)

Falls auf demselben Host **OpenClaw** läuft (multi-agent-Orchestration), wird der Vault dort als Lese-/Schreib-Quelle für zwei Use-Cases eingebunden:

```
   Vault (Host)               Mount                OpenClaw-Container
   /docker/obsidian/vault  ────────────────►   /mnt/obsidian
                                                       │
                                                       ▼
                                          ┌────────────────────────┐
                                          │ Jarvis (Main Agent)    │
                                          │ (User-Anfrage kommt    │
                                          │  via WhatsApp/Butler)  │
                                          └─────────┬──────────────┘
                                                    │
                       Wenn Frage Knowledge-Graph berührt:
                       sessions_spawn oracle
                                                    │
                                                    ▼
                                          ┌────────────────────────┐
                                          │ Oracle (Agent #13)     │
                                          │ kimi-k2.5 / nemotron   │
                                          │  • liest /mnt/obsidian │
                                          │  • antwortet Jarvis    │
                                          │  • schreibt ggf. neue  │
                                          │    Knoten zurück       │
                                          └────────────────────────┘

   ──── nachts 03:00 Berlin (OpenClaw-internes Cron) ────

                                          ┌────────────────────────┐
                                          │ Dreaming Sweep         │
                                          │ Light → REM → Deep     │
                                          │  liest: STM + Vault    │
                                          │  schreibt: MEMORY.md   │
                                          │           DREAMS.md    │
                                          └────────────────────────┘

   Effekt: Terminal-Claude legt tagsüber neue Knoten im Vault ab
           → Oracle kann sie tagsüber on-demand zitieren
           → Dreaming nimmt sie nachts in Jarvis' LTM auf
           → nächster Morgen: Jarvis "weiß" was im Terminal passiert ist
           (ohne dass Martin etwas weiterleiten musste).
```

## 3. Effekt mit Zahlen (warum dieses System)

Quelle der Zahlen: [[CLAUDE.md Lean Refactoring (2026-05-10)]] + Verifikation via `wc -l /root/CLAUDE.md` am Tag dieser Datei.

### 3.1 Direkte Ersparnis (belegt)

| Metrik | Vor Refactoring (≤ 2026-05-10) | Nach Refactoring | Δ |
|---|---|---|---|
| `CLAUDE.md` Zeilen | **821** | **164** (heute, 2026-05-12) | −657 (−80 %) |
| `CLAUDE.md` Tokens (geschätzt, ~13 Z./100 Tok.) | ~10.700 | ~2.450 | **−8.250** |
| Pro Turn dauerhaft gespart | — | **~8.250 Tokens** | jeder einzelne Turn |
| Bei 50 Turns/Tag (typische aktive Session) | — | ~412.500 Tokens/Tag | ≈ 12,4 Mio/Monat |

### 3.2 Indirekte Ersparnis (qualitativ, nicht exakt beziffert)

Vault-Knoten werden nur on-demand geladen. Typische `grep`-Antwort: 50–300 Zeilen aus einem Knoten (~500–3000 Tokens). Eine Session mit 3–5 Lookups kostet also ~2k–15k Tokens *zusätzlich* — aber nur in den Sessions, in denen das Wissen wirklich gebraucht wird. Netto bleibt die Ersparnis deutlich positiv.

### 3.3 Kausal-Kette — die §3.1-Zahl *ist* die Graph-Ersparnis

Eine direkte A/B-Messung ("Sessions mit Graph vs. ohne") gibt es nicht. Aber die §3.1-Zahl ist **kausal an den Graphen gebunden**, nicht an das Refactoring allein:

- Die 657 ausgelagerten CLAUDE.md-Zeilen waren nur deshalb auslagerbar, weil Vault + Graph + MCP existieren. Ohne diese Lookup-Infrastruktur müsste das operative Wissen entweder in CLAUDE.md bleiben (= keine Ersparnis) oder wäre für künftige Sessions verloren (= keine Wahl, weil unwiederfindbar).
- Die ~8.250 Tokens/Turn sind damit defacto die **untere Schranke** der Graph-Ersparnis: das, was dauerhaft messbar gespart wird, weil der Graph das Auslagern überhaupt praktikabel macht.
- **Nicht** in §3.1 enthalten: Re-Discovery-Kosten, die ohne Graph entstünden (Claude müsste pro Session Bekanntes neu herausfinden — Schätzung weitere ~5–20k Tokens/Tag, nicht gemessen). Diese Größenordnung erklärt, warum sich der Graph "subjektiv" deutlich stärker anfühlt als die nackten 8.250/Turn.

### 3.4 Ehrliche Caveats

- Token-Zahlen sind **Schätzungen** (kein Anthropic-Tokenizer-API-Call). Realität liegt typischerweise ±10 %.
- Die 50-Turns/Tag-Annahme kommt aus Beobachtung der Session-Aktivität; je nach Nutzungsmuster kann der Effekt deutlich kleiner sein.
- Der LLM-Graph-Build kostet **separate** Tokens (auf OpenRouter free tier — also $0, aber Rate-Limits können den nächtlichen Lauf bremsen). Nicht mit der CLAUDE.md-Ersparnis gegenrechnen.

## 4. Komponenten im Detail

### 4.1 Obsidian-Vault

- **Pfad Host**: `/docker/obsidian/vault/` (flach, keine Unterordner — Obsidian-Standard für maximale Verlinkung).
- **Container**: `obsidian` (Image `lscr.io/linuxserver/obsidian`, Ports `127.0.0.1:3000/3001`).
- **Reverse-Proxy**: Nginx unter `/etc/nginx/sites-enabled/obsidian`, mit Let's-Encrypt-SSL für `obsidian.<your-domain>`.
- **Auth**: KasmVNC eigene Auth (Nginx-Basic-Auth wurde **entfernt**, weil Safari Username-Trimming-Bug — siehe [[Session 2026-04-17 — Graphify Obsidian Oracle Abschluss]]).
- **Knoten-Schema** (Pflicht für Auto-Konsumierbarkeit):
  ```yaml
  ---
  author: claude-code | oracle | jarvis | user | graphify
  date: 2026-05-12
  type: problem-solution | plan | discussion | reference
  tags: [..., graphify/manual]
  ---
  ```
- **Knoten-Sektionen** (je nach `type`):
  - `problem-solution`: Problem / Root Cause / Solution / Prevention / Connections
  - `plan`: Goal / Steps / Outcome / Connections
  - `discussion`: Context / Key Points / Decisions / Connections
- **Verlinkung**: Mindestens 1–2 `[[Wiki-Links]]` pro Knoten — der Graph entsteht aus diesen Links plus LLM-extrahierten Semantik-Kanten.

### 4.2 Graphify-Bridge

- **Pfad**: `/root/graphify-bridge/`
- **Aufbau**:
  ```
  graphify-bridge/
  ├── conversations/          ← Konvertierte Markdown-Sessions (Korpus)
  ├── graphify-out/
  │   ├── graph.json          ← Vom MCP-Server gelesen
  │   ├── graph.html          ← Visualisierung
  │   ├── GRAPH_REPORT.md
  │   ├── cache/              ← Semantic-Cache (verhindert LLM-Re-Calls)
  │   └── manifest.json
  ├── venv/                   ← Python venv mit `graphify`-Lib
  ├── convert-conversations.py← JSONL → MD
  ├── llm-update.py           ← MD → graph.json (OpenRouter free)
  ├── daily-update.sh         ← Wrapper (Cron-Entry-Point)
  ├── mcp-server.sh           ← stdio-Server (von Claude Code aufgerufen)
  ├── .last-hash              ← Change-Detection
  └── NEEDS_GRAPH_UPDATE      ← Flag (nur wenn letzter Lauf failed)
  ```
- **Aktueller Korpus**: 105 Sessions, **2193 Nodes / 186 Communities** (Stand 2026-05-12, größer als die im Vault zitierten "235/48" aus 2026-04-17).
- **LLM**: OpenRouter, Primary `minimax/minimax-m2.5:free`, Fallback `nvidia/nemotron-nano-9b-v2:free`. ENV: `OPENROUTER_API_KEY` muss als Umgebungsvariable im Cron-Lauf gesetzt sein (in `/etc/environment` oder direkt im daily-update.sh).

### 4.3 MCP-Server `graphify`

- **Aufruf** (in `/root/.claude.json`):
  ```json
  "graphify": {
    "type": "stdio",
    "command": "/root/graphify-bridge/mcp-server.sh",
    "args": [],
    "env": {}
  }
  ```
- **Innere Logik** (`mcp-server.sh`):
  ```bash
  exec /root/graphify-bridge/venv/bin/python3 -c \
    "from graphify.serve import serve; serve('/root/graphify-bridge/graphify-out/graph.json')"
  ```
- **Tools für Claude** (deferred — werden erst on-demand geladen, kein Boot-Kosten):
  - `get_node(name)`, `get_neighbors(name)`, `get_community(name)`
  - `query_graph(query)`, `shortest_path(a, b)`
  - `god_nodes()` (am stärksten vernetzte), `graph_stats()`

### 4.4 GitHub-Repo-Spiegel (`/root/jarvis-repo/`)

- **Remote**: `https://github.com/MartinKusterer/J.A.R.V.I.S.`
- **Sync-Script**: `/root/jarvis-repo/scripts/sync-from-server.sh` (idempotent, secret-safe).
- **Whitelist** (gespiegelt):
  - `/docker/openclaw-<id>/` (Scripts, Compose, Config, Sandbox-Image, Top-Level-Docs)
  - `/docker/openclaw-<id>/data/.openclaw/workspace/[A-Z]*.md` (kanonische Workspace-Docs)
  - `/docker/obsidian/vault/*.md` (kompletter Knowledge-Graph)
  - `/root/pixel-office/`, `/root/jarvis-monitor/`, `/root/webclipper/`
  - Host-`crontab -l` → `host-config/crontab.txt`
- **Hard Excludes** (Secrets + Runtime-State):
  ```
  .env, .env.*, auth-profiles.json, *.key, *.pem, *.crt, *.p12, *.pfx,
  credentials.json, service-account.json, node_modules/, __pycache__/,
  *.pyc, *.sqlite, *.sqlite-*, *.db, *.log, *.jsonl, *.lock, *.bak, *.broken
  ```
- **Modi**:
  ```bash
  sync-from-server.sh              # nur rsync (kein commit)
  sync-from-server.sh --dry-run    # was würde sich ändern
  sync-from-server.sh --commit     # rsync + auto-commit
  sync-from-server.sh --commit --push  # rsync + commit + push origin/main
  ```

### 4.5 Cron-Jobs (relevant für dieses System)

**Host-Crontab** (Producer-Side):

```cron
# Knowledge-Graph täglich rebuilden (03:30 Berlin = 02:30 UTC)
30 3 * * * /root/graphify-bridge/daily-update.sh

# GitHub-Repo wöchentlich syncen — Backstop, nicht primärer Sync (Mon 04:00 UTC)
0 4 * * 1 /root/jarvis-repo/scripts/sync-from-server.sh --commit --push \
          >> /var/log/jarvis-repo-sync.log 2>&1
```

**OpenClaw-interner Scheduler** (Consumer-Side, optional — nur falls Jarvis-Dreaming gewünscht):

Dreaming läuft **nicht** als Host-Cron, sondern als plugin-internes Schedule im OpenClaw-Gateway. Config-Pfad:

```bash
docker exec openclaw-<id>-openclaw-1 \
  openclaw config get plugins.entries.memory-core.config.dreaming
# → { "enabled": true, "timezone": "Europe/Berlin", "phases": { ... } }
```

Default-Timeout des Gateway-SystemEvent-Schedulers wurde von 10 min auf 30 min hochgepatcht, sonst killt der Watchdog die Deep-Phase mitten in der Promotion (siehe [[cron-systemevent-timeout-patch-20260420]]). Bei OpenClaw-Updates muss der Patch ggf. re-appliziert werden.

### 4.6 Oracle-Agent (Consumer-Side, OpenClaw-spezifisch)

- **Was**: OpenClaw-Agent (Index 13), Spezialist für Vault-Queries.
- **Modell**: `nvidia/moonshotai/kimi-k2.5` primary, `nvidia/nvidia/llama-3.1-nemotron-ultra-253b-v1:free` fallback. Synthese-starke Modelle (kein Coder-Pool — Oracle macht semantische Verknüpfung, keine Tool-Heavy-Calls).
- **Aufruf**: Jarvis delegiert via `sessions_spawn oracle "<query>"`. Funktioniert nur, wenn `tools.agentToAgent.enabled = true` UND `/data/.openclaw/workspace/AGENTS.md` Oracle in der Delegations-Sektion listet (siehe [[Oracle Reaktivierung - Model + Delegations-Fix (2026-04-20)]] — beide Punkte waren bis 2026-04-20 falsch konfiguriert, Oracle lag de-facto brach).
- **Vault-Zugriff**: Container-Mount `/mnt/obsidian/` = Host-`/docker/obsidian/vault/`. Oracle hat `workspaceAccess: "rw"` für `/mnt/obsidian/` — kann **lesen UND schreiben**.
- **Schreibt zurück**: Synthesen, Wiki-Link-Reparaturen, neue Diskussions-Knoten aus Jarvis-Konversationen. Frontmatter `author: oracle` macht Bot-Edits identifizierbar.
- **Off-Switch**: `tools.agentToAgent.enabled = false` deaktiviert Delegation (Jarvis fällt zurück auf "weiß ich nicht").

### 4.7 Jarvis Dreaming-Phase (Consumer-Side, OpenClaw-spezifisch)

- **Was**: Nächtliche Memory-Consolidation, drei Phasen seriell. Ersetzte das alte `qmd`-System (CPU-Überlast-Inzident — siehe [[qmd CPU Overload Incident]]).
- **Trigger**: OpenClaw-interner Cron `0 3 * * *` Europe/Berlin (im Gateway, nicht im Host-Crontab). Default-Timeout 30 min (von 10 min hochgepatcht, siehe [[cron-systemevent-timeout-patch-20260420]]).
- **Phasen**:
  - **Light**: STM-Signale aus `/data/.openclaw/lcm.db` sammeln, dedup, Kandidaten für Promotion stagen.
  - **REM**: Themen + Muster aus Tagessessions extrahieren, Reflexions-Signale.
  - **Deep**: Scored Promotion (`minScore=0.8`, `minRecallCount=3`, `minUniqueQueries=3`) → `MEMORY.md` (LTM).
- **Schreibt**:
  - `MEMORY.md` — LTM-Zusammenfassung, in alle Sandboxes via Host-Pfade gesymlinked, von Agents als Read-Only-Quelle verwendet.
  - `DREAMS.md` (Dream Diary) — human-readable Bericht jeder Nacht.
- **Liest Vault**: Vault-Knoten (Mount `/mnt/obsidian/`) fließen mit in die Deep-Phase ein, weil Dreaming sie als zusätzliches Korpus für Promotion-Scoring berücksichtigt. → **Was Terminal-Claude tagsüber an Vault-Knoten schreibt, ist nächsten Morgen in Jarvis' Langzeit-Gedächtnis** ohne weitere Aktion.
- **Diagnose bei Fehlschlag**: Dreaming-Log im OpenClaw-Gateway. Wenn `MEMORY.md` Drift zeigt (z.B. >12 kB durch Self-Reference-Spam → OOM), siehe Memory-Note `jarvis-memoryMd-overflow-20260420.md`.

### 4.8 `CLAUDE.md` — die Lean-Regel

- **Pfad**: `/root/CLAUDE.md` (projekt-spezifisch, wird in jedem Turn geladen).
- **Hartes Limit**: **200 Zeilen**, in einer Meta-Regel am Anfang der Datei verankert. Diese Regel darf laut Feedback-Memory **nur Martin selbst** widerrufen.
- **Was rein darf**: Session-Start-Checks, Workflow-Prinzipien, Knowledge-Graph-Methodik, Top-7-Pfade, Critical Risiko-Rules, Quick Health Check, Vault-Pointer-Tabelle.
- **Was raus muss**: Tägliche Commands, Architektur-Details, Troubleshooting-Recipes, Cron-Tabellen → in **Vault-Reference-Knoten** (siehe [[OpenClaw Operations Reference (2026-05-10)]], [[OpenClaw Architecture Reference (2026-05-10)]], [[OpenClaw Workflows Reference (2026-05-10)]], [[OpenClaw Troubleshooting Reference (2026-05-10)]]).
- **Aktuell**: 164 Zeilen (leichte Drift seit Refactoring von 154 → 164; akzeptabel solange ≤200, sonst auslagern).

## 5. Tägliche Logik (was passiert wann)

### 5.1 03:30 — `daily-update.sh`

1. Hash aller `.md`-Dateien in `conversations/` **vorher** berechnen (sha256).
2. `convert-conversations.py` liest Claude-Code-JSONL (`~/.claude/projects/-root/`) und schreibt neue/aktualisierte Markdown-Dateien.
3. Hash **nachher** berechnen.
4. Wenn unverändert → exit 0 (nichts zu tun).
5. Wenn geändert → `NEEDS_GRAPH_UPDATE`-Flag setzen, `llm-update.py` starten.
6. `llm-update.py`:
   - Inkrementelle Detection (`detect_incremental`) — nur neue/geänderte Files.
   - LLM-Call pro Datei (Cache-aware via `check_semantic_cache`).
   - Build → Cluster (Community-Detection) → Report.
   - Bei Erfolg: `NEEDS_GRAPH_UPDATE` entfernen.
   - Bei Fehler: Flag bleibt → Session-Start-Check (CLAUDE.md) sieht Flag → Claude meldet es.

### 5.2 Manuell nach Server-Change — `sync-from-server.sh --commit --push`

Soll **proaktiv** nach jeder Änderung an gespiegelten Files laufen. Der wöchentliche Cron ist nur Backstop, weil das Repo zwischen 2026-03-17 und 2026-05-10 zwei Monate lang nicht synchronisiert war.

### 5.3 Vault-Knoten anlegen — bei Plan / Problem / Entscheidung

Nicht automatisch. Claude legt nach jedem nontrivialen Fix einen Knoten in `/docker/obsidian/vault/` an, mit Frontmatter + Connections. Der nächtliche Graph-Build fasst Konversationen, der Knoten ist die kuratierte Form.

## 6. Reproduktion — Schritt-für-Schritt für einen leeren Server

> Voraussetzung: Linux-Server mit Docker, Python 3.11+, git, rsync, certbot. SSH-Zugang als root.

### 6.1 Obsidian-Vault starten

```bash
mkdir -p /docker/obsidian/vault /docker/obsidian/config
docker run -d --name obsidian \
  --restart unless-stopped \
  -p 127.0.0.1:3000:3000 -p 127.0.0.1:3001:3001 \
  -v /docker/obsidian/vault:/config/vault \
  -v /docker/obsidian/config:/config \
  -e PUID=1000 -e PGID=1000 -e TZ=Europe/Berlin \
  lscr.io/linuxserver/obsidian:latest
```

Nginx-Vhost + certbot (Domain anpassen):
```bash
cat > /etc/nginx/sites-available/obsidian <<'EOF'
server {
    listen 80;
    server_name obsidian.<your-domain>;
    location / {
        proxy_pass http://127.0.0.1:3001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF
ln -sf /etc/nginx/sites-available/obsidian /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d obsidian.<your-domain> --non-interactive --agree-tos -m <email>
```

### 6.2 Graphify-Bridge installieren

```bash
mkdir -p /root/graphify-bridge && cd /root/graphify-bridge
python3 -m venv venv
./venv/bin/pip install graphify httpx networkx
```

`convert-conversations.py`, `llm-update.py`, `daily-update.sh`, `mcp-server.sh` aus dem Repo holen:
```bash
cd /root
git clone https://github.com/MartinKusterer/J.A.R.V.I.S. jarvis-repo
cp jarvis-repo/scripts/*.sh /root/graphify-bridge/ 2>/dev/null || true
# Plus die Bridge-spezifischen Skripte aus jarvis-repo/openclaw/scripts/ falls vorhanden
chmod +x /root/graphify-bridge/*.sh
```

OpenRouter-Key setzen (free tier — Account auf openrouter.ai anlegen):
```bash
echo 'OPENROUTER_API_KEY=sk-or-v1-...' >> /etc/environment
```

### 6.3 MCP-Server in Claude Code registrieren

In `~/.claude.json` unter `mcpServers` einfügen:
```json
"graphify": {
  "type": "stdio",
  "command": "/root/graphify-bridge/mcp-server.sh",
  "args": [],
  "env": {}
}
```

Claude Code neu starten. Test: in einer Session `mcp__graphify__graph_stats` aufrufen.

### 6.4 GitHub-Repo-Spiegel einrichten

```bash
cd /root/jarvis-repo
git remote set-url origin git@github.com:<your-user>/<your-repo>.git
git config user.email "<email>"
git config user.name "<name>"

# Whitelist in scripts/sync-from-server.sh an eigene Pfade anpassen (RSYNC-Sections)
# Dry-Run testen
./scripts/sync-from-server.sh --dry-run

# Live
./scripts/sync-from-server.sh --commit --push
```

### 6.5 Cron-Jobs setzen

```bash
crontab -e
# Hinzufügen:
30 3 * * * /root/graphify-bridge/daily-update.sh
0 4 * * 1 /root/jarvis-repo/scripts/sync-from-server.sh --commit --push >> /var/log/jarvis-repo-sync.log 2>&1
```

### 6.6 `CLAUDE.md` mit Lean-Pattern anlegen

`/root/CLAUDE.md` mit folgendem Skeleton (Details: [[CLAUDE.md Lean Refactoring (2026-05-10)]]):

```markdown
# CLAUDE.md

> Meta-Regel: Maximal 200 Zeilen. Operatives in Vault-Reference-Knoten.

## Session-Start-Checks
- NEEDS_GRAPH_UPDATE-Flag prüfen → /root/graphify-bridge/NEEDS_GRAPH_UPDATE
- Bei Fragen zu Architektur/Bugs: zuerst grep im Vault, NICHT nachfragen
- Sprache: <eure Wahl>

## Workflow & Core Principles
1. Plan Mode default für ≥3 Schritte
2. Subagents für Research (Context-Window sauber halten)
3. Self-Improvement Loop: nach Korrektur → Memory-Note

## Knowledge Graph (Obsidian Vault)
- Vault: /docker/obsidian/vault/ (flach)
- Web-UI: https://obsidian.<your-domain>
- MCP: graphify (stdio)
- Auto-Update: täglich 03:30 via /root/graphify-bridge/daily-update.sh
- Knoten anlegen IMMER bei: Plan, Problem/Bug, Entscheidung
- Frontmatter-Schema: siehe §4.1 dieser Datei

## Critical Paths (Top-7) und Vault-Pointer-Tabelle
... <projektspezifisch> ...
```

## 7. Bewusste Trade-offs / Was es nicht löst

- **Kein automatisches LLM-Embedding für jeden Vault-Knoten**: `daily-update.sh` arbeitet nur auf den **Session-Konversationen**, nicht direkt auf den händisch angelegten Vault-Knoten. Diese werden über `[[Wiki-Links]]` verbunden und beim nächsten Session-Run mit-eingelesen.
- **Keine Echtzeit-Synchronisation**: Der Graph wird **einmal pro Nacht** gebaut. Untertags-Änderungen am Korpus sind nicht im Graph, bis 03:30.
- **Free-Tier-Rate-Limits**: OpenRouter `:free`-Modelle können bei großen Korpora (>50 neue Files/Nacht) Rate-Limits treffen. `llm-update.py` retry'ed mit Fallback-Modell, kann aber trotzdem partiell fehlschlagen → `NEEDS_GRAPH_UPDATE`-Flag bleibt.
- **Kein Geheimnis-Schutz im Vault**: Wer Vault-Zugriff hat, sieht alles. Sensible Passwörter / API-Keys gehören in `.env`-Files, die per Excludes-Liste **nie** gespiegelt werden.
- **Token-Zahlen sind Schätzungen**: ±10 % Realitäts-Abweichung wahrscheinlich. Wer es exakt will, sollte den Anthropic-Tokenizer-Endpoint nutzen.
- **CLAUDE.md-Drift**: Die 200-Zeilen-Regel wird nicht automatisch durchgesetzt. Aktuell 164 (war: 154) — wer kein Auge drauf hat, riskiert wieder >300 Zeilen über Monate.
- **Consumer-Side ist OpenClaw-spezifisch**: §4.6 (Oracle) und §4.7 (Dreaming) setzen einen laufenden OpenClaw-Container voraus. Ohne OpenClaw funktioniert die Producer-Seite (§4.1–§4.5) eigenständig — der Vault wird dann nur von Terminal-Claude gelesen, es gibt keinen nightly Memory-Consolidation-Loop. Wer das Pattern ohne OpenClaw nachbauen will: einen eigenen Cron-Job schreiben, der den Vault per LLM zusammenfasst und in eine eigene `MEMORY.md` schreibt.
- **Oracle-Antwort hängt am LLM-Anbieter**: Oracle nutzt OpenRouter `:free`/NVIDIA-Modelle für die Synthese. Bei Provider-Ausfall fällt Jarvis still zurück auf "kein Knoten gefunden", obwohl es im Vault stünde — schwer von Cache-Miss zu unterscheiden. Im Zweifel: Oracle-Session-Log im OpenClaw-Gateway prüfen.

## 8. Wartung

### 8.1 Wöchentliche Checks
- `wc -l /root/CLAUDE.md` — ≤200?
- `tail -100 /var/log/graphify/daily-update.log` — saubere Läufe?
- `tail -50 /var/log/jarvis-repo-sync.log` — keine Sync-Konflikte?
- `ls /root/graphify-bridge/NEEDS_GRAPH_UPDATE` — Flag weg?
- Consumer-Side (falls OpenClaw): hat Jarvis-Dreaming neue `DREAMS.md`-Einträge der letzten 3 Nächte? `grep -c "^## " /docker/obsidian/vault/DREAMS.md*` — wenn Wachstum stagniert: Dreaming-Crash, Gateway-Logs prüfen.
- Consumer-Side: spawnt Jarvis Oracle bei passenden Queries? `docker logs openclaw-<id>-openclaw-1 | grep -i "sessions_spawn.*oracle"` — null Hits über Tage ist verdächtig (siehe Inzident 2026-04-18, [[Oracle Reaktivierung - Model + Delegations-Fix (2026-04-20)]]).

### 8.2 Bei Hash-Drift / Korpus-Reset
```bash
rm /root/graphify-bridge/.last-hash
/root/graphify-bridge/daily-update.sh
```

### 8.3 Bei MCP-Server-Crash
1. `jq . /root/graphify-bridge/graphify-out/graph.json | head` — JSON valide?
2. `/root/graphify-bridge/mcp-server.sh` manuell starten, stderr beobachten.
3. Claude Code neu starten (lädt MCP-Config beim Start).

### 8.4 Bei GitHub-Sync-Failure
- **Nie** `git push --force` als Reflex. Erst Ursache verstehen (meist ein neuer File-Typ den die Whitelist nicht kennt, oder ein versehentlich gestaged Secret).
- Skript um neuen Pfad erweitern → committen → erneut syncen.

## 9. Quellen / Connections

- [[CLAUDE.md Lean Refactoring (2026-05-10)]] — Begründung + Zahlen für das Lean-Pattern
- [[OpenClaw Architecture Reference (2026-05-10)]] — Cron-Tabellen, Bridge-Pfade
- [[Session 2026-04-17 — Graphify Obsidian Oracle Abschluss]] — Setup-Verlauf (Chat-Form)
- [[OpenClaw Operations Reference (2026-05-10)]] — Tägliche Commands
- [[OpenClaw Workflows Reference (2026-05-10)]] — Mehrstufige Procedures
- [[OpenClaw Troubleshooting Reference (2026-05-10)]] — Failure-Recipes
- [[Graphify (safishamsi)]] — Bibliothek
- [[Knowledge Graph als Kommunikationsbrücke (Terminal - OpenClaw)]] — Designidee
- `/root/CLAUDE.md` — Boot-Instruktionen (lean)
- `/root/graphify-bridge/daily-update.sh` — Cron-Entry
- `/root/jarvis-repo/scripts/sync-from-server.sh` — GitHub-Sync
- `/root/.claude.json` — MCP-Config (graphify-Eintrag)
- Memory `feedback-claude-md-lean.md` — Persistente Regel für Future-Claude
- Memory `feedback-jarvis-repo-sync.md` — Sync-Disziplin

## 10. Übergabe-Status & Limitationen dieser Datei

- Geschrieben am 2026-05-12 von Claude Code (Opus 4.7) auf Anfrage von Martin.
- Token-Zahlen aus [[CLAUDE.md Lean Refactoring (2026-05-10)]] übernommen + heute neu verifiziert mit `wc -l /root/CLAUDE.md`.
- Graph-Stats (Nodes/Communities) **live** ausgelesen aus `graphify-out/graph.json`, nicht aus älteren Vault-Notes (die "235 Nodes" zitieren — veraltet).
- Diese Datei ist **kein** Ersatz für: Anthropic-API-Doku, Obsidian-Doku, Docker-Doku, OpenRouter-Account-Anlage. Sie deckt nur das **Zusammenspiel** der Komponenten ab.
- Wenn euer Setup von Martins abweicht (anderer Container-Stack, anderes Repo, andere Domain), passt §6 Schritt-für-Schritt-Block entsprechend an. Die Logik (Boot-Kontext minimieren, On-demand-Lookup, Daily-Graph-Rebuild, Repo-Spiegel als Backup) bleibt gleich.
