# graphify-claudecode-openclaw-bridge

A nightly bridge that turns [Claude Code](https://docs.claude.com/en/docs/claude-code) conversation history into a queryable knowledge graph, using [graphify](https://github.com/safishamsi/graphify) as the extraction + clustering engine and OpenRouter free models for semantic enrichment.

> **Upstream engine**: [safishamsi/graphify](https://github.com/safishamsi/graphify) (PyPI: `graphifyy`). This repo is a glue layer around it — it does not fork or vendor graphify.

## What it does

1. **`convert-conversations.py`** — converts Claude Code JSONL session logs (`~/.claude/projects/.../*.jsonl`) and memory notes into clean Markdown files in `conversations/`.
2. **`llm-update.py`** — runs incremental extraction over `conversations/` using OpenRouter (`minimax/minimax-m2.5:free` primary, `nvidia/nemotron-nano-9b-v2:free` fallback), merges results into an existing graph, re-clusters, and writes `graph.json`, `graph.html`, and `GRAPH_REPORT.md` into `graphify-out/`. Uses graphify's own cache, build, cluster, and report APIs — no fork needed.
3. **`daily-update.sh`** — cron wrapper. Detects changes via SHA of `conversations/`, sets a `NEEDS_GRAPH_UPDATE` flag, triggers `llm-update.py`. Logs to `/var/log/graphify/`.
4. **`mcp-server.sh`** — exposes the resulting `graph.json` to Claude Code as an [MCP](https://modelcontextprotocol.io/) stdio server (via graphify's built-in `graphify.serve`).

Designed for a setup where Claude Code runs alongside [OpenClaw](https://github.com/hostinger/hvps-openclaw) agents on the same host, but the conversion + update logic works with any Claude Code installation.

> **Want the full picture?** See **[HANDOFF.md](HANDOFF.md)** — a 400-line walk-through covering the whole knowledge system (Obsidian vault + graphify-bridge + MCP server + GitHub mirror), including architecture diagram, token-cost numbers, step-by-step setup for an empty server, and the design trade-offs. This README is the technical quick-start; HANDOFF.md is the design doc.

## Why this exists

Claude Code stores conversations as JSONL on disk. Graphify expects Markdown documents. Running `/graphify --update` interactively burns tokens on every refresh. This bridge:

- converts JSONL → Markdown once per night
- uses **free** OpenRouter models instead of Claude tokens for the extraction step
- keeps the resulting graph current in the background
- exposes it via MCP so Claude can query its own conversation history during work

## Requirements

- Python 3.12+
- `graphifyy` (PyPI package, import name `graphify`) v0.4.18 or newer
- `httpx` (for OpenRouter API calls)
- `networkx`
- An [OpenRouter](https://openrouter.ai) account and API key (free tier works)

## Setup

```bash
git clone https://github.com/MartinKusterer/graphify-claudecode-openclaw-bridge.git
cd graphify-claudecode-openclaw-bridge

python3 -m venv venv
source venv/bin/activate
pip install graphifyy httpx networkx
```

### Configure paths

The scripts have **hard-coded paths** tuned to one specific host layout:

| Constant | File | Default | What to change it to |
|----------|------|---------|----------------------|
| `JSONL_DIR` | `convert-conversations.py` | `/root/.claude/projects/-root` | your Claude Code projects dir |
| `OUTPUT_DIR` | `convert-conversations.py` | `/root/graphify-bridge/conversations` | wherever this repo lives |
| `BRIDGE` | `llm-update.py`, `daily-update.sh` | `/root/graphify-bridge` | wherever this repo lives |
| `LOG_DIR` | `llm-update.py`, `daily-update.sh` | `/var/log/graphify` | a writable log dir |

Edit them once after cloning, or fork and parameterise — pull requests welcome.

### Provide the API key

`llm-update.py` reads `OPENROUTER_API_KEY` from:

1. the environment, or
2. a fallback file at `/docker/openclaw-yykx/.env` (OpenClaw setup convention)

For a generic setup, just export it:

```bash
export OPENROUTER_API_KEY=sk-or-v1-…
```

**Never commit your key.** `.env` files are in `.gitignore`.

### Schedule the nightly update

```cron
30 3 * * *  /path/to/graphify-bridge/daily-update.sh
```

### Wire up the MCP server (optional)

Add to your Claude Code MCP config (`~/.claude.json`):

```json
{
  "mcpServers": {
    "graphify": {
      "command": "/path/to/graphify-bridge/mcp-server.sh"
    }
  }
}
```

## Privacy & security

- **`conversations/` is gitignored.** Claude Code sessions contain personal context, server paths, credentials shown in transcripts, etc. This directory must never be committed. The `.gitignore` enforces this — do not override.
- **`graphify-out/` is also gitignored**, because the extracted graph embeds entity names lifted verbatim from the conversations.
- **No secrets are stored in this repo.** All credentials come from environment variables or files outside the repo.
- Before pushing changes, re-check with `git status` that no `conversations/`, `graphify-out/`, `.env`, or `venv/` files are staged.

If you fork this and run it against your own Claude Code installation, your `conversations/` will look very different from mine, but the same privacy rules apply: keep them local.

## How nightly update flow works

```
~/.claude/projects/-root/*.jsonl
        │
        ▼
convert-conversations.py    ──►   conversations/*.md
        │
        ▼  (SHA changed?)
daily-update.sh sets NEEDS_GRAPH_UPDATE
        │
        ▼
llm-update.py
   ├── graphify.detect.detect_incremental()
   ├── graphify.cache.check_semantic_cache()
   ├── OpenRouter extraction (free models)
   ├── graphify.build.build_from_json() → merge into existing graph
   ├── graphify.cluster.cluster() + score_all()
   ├── graphify.analyze.* (god_nodes, surprises, suggestions)
   └── graphify.report.generate() + export.to_json + to_html
        │
        ▼
graphify-out/{graph.json, graph.html, GRAPH_REPORT.md, cost.json}
        │
        ▼  (queried via MCP)
Claude Code  ─►  mcp-server.sh  ─►  graphify.serve
```

## Related projects

- [safishamsi/graphify](https://github.com/safishamsi/graphify) — the underlying knowledge-graph engine. All credit for the heavy lifting goes there.
- [Claude Code](https://docs.claude.com/en/docs/claude-code) — Anthropic's CLI agent.
- [OpenClaw](https://github.com/hostinger/hvps-openclaw) — multi-agent orchestration host this bridge was built alongside (the bridge does not require it).
- [OpenRouter](https://openrouter.ai) — used here for free-tier model access during extraction.

## License

MIT — see [LICENSE](LICENSE).
