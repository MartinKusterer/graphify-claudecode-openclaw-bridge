#!/usr/bin/env python3
"""Convert Claude Code JSONL conversations to Markdown for Graphify."""

import json
import os
import shutil
from pathlib import Path
from datetime import datetime

JSONL_DIR = Path("/root/.claude/projects/-root")
MEMORY_DIR = JSONL_DIR / "memory"
OUTPUT_DIR = Path("/root/graphify-bridge/conversations")

# Types to extract
CONTENT_TYPES = {"user", "assistant"}
# Types to skip
SKIP_TYPES = {"file-history-snapshot", "permission-mode", "progress", "system", "attachment"}


def extract_text(content):
    """Extract plain text from message content (string or list)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts)
    return ""


def clean_text(text, max_len=2000):
    """Clean text: remove system-reminder tags, truncate if too long."""
    import re
    # Remove <system-reminder>...</system-reminder> blocks
    text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.DOTALL)
    # Remove <command-message>...</command-message> blocks
    text = re.sub(r"<command-message>.*?</command-message>", "", text, flags=re.DOTALL)
    # Remove <command-name>...</command-name> blocks
    text = re.sub(r"<command-name>.*?</command-name>", "", text, flags=re.DOTALL)
    # Remove <*> tags
    text = re.sub(r"<[^>]*>.*?</[^>]*>", "", text, flags=re.DOTALL)
    # Remove <functions>...</functions> blocks (tool definitions)
    text = re.sub(r"<functions>.*?</functions>", "", text, flags=re.DOTALL)
    # Clean up excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    # Truncate very long messages
    if len(text) > max_len:
        text = text[:max_len] + "\n\n[... gekürzt ...]"
    return text


def get_session_topic(messages):
    """Extract topic from first user message."""
    for msg in messages:
        if msg["role"] == "user":
            text = msg["text"][:100].replace("\n", " ").strip()
            if text:
                # Remove markdown/special chars for filename
                return text
    return "Unbekanntes Thema"


def convert_jsonl(jsonl_path):
    """Convert a single JSONL file to a list of messages."""
    messages = []
    session_date = None

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = obj.get("type", "")
            if msg_type not in CONTENT_TYPES:
                continue

            message = obj.get("message", {})
            role = message.get("role", "")
            content = message.get("content", "")
            timestamp = obj.get("timestamp", "")

            text = extract_text(content)
            text = clean_text(text)

            if not text:
                continue

            if timestamp and not session_date:
                try:
                    session_date = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass

            messages.append({
                "role": role,
                "text": text,
                "timestamp": timestamp[:19] if timestamp else "",
            })

    return messages, session_date


def messages_to_markdown(messages, session_date, session_id):
    """Convert messages to Markdown format."""
    if not messages:
        return None

    topic = get_session_topic(messages)
    date_str = session_date.strftime("%Y-%m-%d") if session_date else "unknown"

    lines = []
    lines.append(f"# Session: {date_str} — {topic[:80]}")
    lines.append("")
    lines.append(f"**Datum**: {date_str}")
    lines.append(f"**Session-ID**: {session_id}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in messages:
        if msg["role"] == "user":
            lines.append(f"## Martin")
            lines.append("")
            lines.append(msg["text"])
            lines.append("")
        elif msg["role"] == "assistant":
            lines.append(f"## Claude")
            lines.append("")
            lines.append(msg["text"])
            lines.append("")

    return "\n".join(lines)


def main():
    # Clean output dir
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)

    # Convert JSONL files
    converted = 0
    skipped = 0

    for jsonl_file in sorted(JSONL_DIR.glob("*.jsonl")):
        session_id = jsonl_file.stem
        messages, session_date = convert_jsonl(jsonl_file)

        # Skip very short sessions (< 2 meaningful messages)
        if len(messages) < 2:
            skipped += 1
            continue

        md_content = messages_to_markdown(messages, session_date, session_id)
        if not md_content:
            skipped += 1
            continue

        date_str = session_date.strftime("%Y-%m-%d") if session_date else "unknown"
        short_id = session_id[:8]
        filename = f"session-{date_str}-{short_id}.md"

        output_path = OUTPUT_DIR / filename
        output_path.write_text(md_content, encoding="utf-8")
        converted += 1

    # Copy memory files (except MEMORY.md index)
    memory_copied = 0
    if MEMORY_DIR.exists():
        memory_out = OUTPUT_DIR / "memory"
        memory_out.mkdir(exist_ok=True)
        for md_file in MEMORY_DIR.glob("*.md"):
            if md_file.name == "MEMORY.md":
                continue
            shutil.copy2(md_file, memory_out / md_file.name)
            memory_copied += 1

    print(f"Konvertiert: {converted} Sessions")
    print(f"Übersprungen: {skipped} Sessions (zu kurz)")
    print(f"Memory-Dateien: {memory_copied} kopiert")
    print(f"Output: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
