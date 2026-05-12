#!/usr/bin/env python3
"""Headless LLM-driven graph update for /root/graphify-bridge/conversations.

Replaces the need to run /graphify --update inside Claude Code.
Uses OpenRouter free tier (minimax-m2.5:free) for semantic extraction.
Reuses graphify's cache/build/cluster/report APIs.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

import httpx

from graphify.detect import detect_incremental, save_manifest
from graphify.cache import check_semantic_cache, save_semantic_cache
from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate
from graphify.export import to_json, to_html
from networkx.readwrite import json_graph

BRIDGE = Path("/root/graphify-bridge")
CORPUS = BRIDGE / "conversations"
OUT = BRIDGE / "graphify-out"
FLAG = BRIDGE / "NEEDS_GRAPH_UPDATE"
LOG_DIR = Path("/var/log/graphify")
LOG_FILE = LOG_DIR / "llm-update.log"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_PRIMARY = os.environ.get("GRAPHIFY_LLM_MODEL", "minimax/minimax-m2.5:free")
MODEL_FALLBACK = "nvidia/nemotron-nano-9b-v2:free"
MAX_RETRIES = 2
REQUEST_TIMEOUT = 120.0


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as fh:
        fh.write(line + "\n")


def load_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    env_path = Path("/docker/openclaw-yykx/.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("OPENROUTER_API_KEY not found")


EXTRACTION_PROMPT = """You extract a knowledge graph fragment from the given document.
Output ONLY valid JSON matching the schema below - no explanation, no markdown fences, no preamble.

File path: {path}
File content:
---
{content}
---

Rules:
- EXTRACTED: relationship explicit in source
- INFERRED: reasonable inference
- AMBIGUOUS: uncertain

Extract named concepts, entities, citations. Also extract rationale - sections that explain WHY a decision was made, trade-offs chosen, or design intent. These become nodes with `rationale_for` edges pointing to the concept they explain.

Semantic similarity: if two concepts solve the same problem or represent the same idea without any structural link, add a `semantically_similar_to` edge marked INFERRED with a confidence_score reflecting how similar they are (0.6-0.95).

Hyperedges: if 3+ nodes clearly participate together in a shared concept/flow/pattern not captured by pairwise edges alone, add a hyperedge to `hyperedges`. Use sparingly. Max 3 per file.

If YAML frontmatter exists (--- ... ---), copy source_url, captured_at, author, contributor onto every node from that file.

confidence_score REQUIRED on every edge:
- EXTRACTED: 1.0
- INFERRED: 0.6-0.9 (most cases), 0.4-0.5 (weak/speculative)
- AMBIGUOUS: 0.1-0.3

Node ID format: lowercase, only `[a-z0-9_]`. Format: `{{stem}}_{{entity}}` where stem is filename without extension, entity is symbol name, both normalized.

Output exactly this JSON (no other text, no fences):
{{"nodes":[{{"id":"node_id","label":"Human Readable Name","file_type":"document","source_file":"{path}","source_location":null,"source_url":null,"captured_at":null,"author":null,"contributor":null}}],"edges":[{{"source":"node_id","target":"node_id","relation":"references|cites|conceptually_related_to|shares_data_with|semantically_similar_to|rationale_for","confidence":"EXTRACTED|INFERRED|AMBIGUOUS","confidence_score":1.0,"source_file":"{path}","source_location":null,"weight":1.0}}],"hyperedges":[]}}"""


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def call_llm(client: httpx.Client, api_key: str, prompt: str, model: str) -> tuple[str, int, int]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://graphify-bridge.local",
        "X-Title": "graphify-bridge-nightly",
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 4096,
    }
    r = client.post(OPENROUTER_URL, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return content, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def extract_one(client: httpx.Client, api_key: str, path: Path) -> tuple[dict, int, int]:
    content = path.read_text(errors="ignore")
    if len(content) > 60_000:
        content = content[:60_000] + "\n[...truncated...]"
    prompt = EXTRACTION_PROMPT.format(path=str(path), content=content)
    last_err = None
    for model in (MODEL_PRIMARY, MODEL_FALLBACK):
        for attempt in range(MAX_RETRIES):
            try:
                raw, pt, ct = call_llm(client, api_key, prompt, model)
                result = extract_json(raw)
                result.setdefault("nodes", [])
                result.setdefault("edges", [])
                result.setdefault("hyperedges", [])
                return result, pt, ct
            except Exception as e:
                last_err = e
                log(f"  attempt {attempt + 1}/{MAX_RETRIES} on {model} failed: {type(e).__name__}: {str(e)[:200]}")
                time.sleep(2 ** attempt)
    raise RuntimeError(f"all extraction attempts failed for {path}: {last_err}")


def main() -> int:
    log("=== llm-update start ===")

    incremental = detect_incremental(CORPUS)
    new_total = incremental.get("new_total", 0)
    deleted = set(incremental.get("deleted_files", []))

    if new_total == 0 and not deleted:
        log("no changes — nothing to do")
        FLAG.unlink(missing_ok=True)
        return 0

    log(f"{new_total} new/changed file(s), {len(deleted)} deleted")

    all_files = [f for files in incremental["new_files"].values() for f in files]
    cached_nodes, cached_edges, cached_hyperedges, uncached = check_semantic_cache(all_files)
    log(f"cache: {len(all_files) - len(uncached)} hit, {len(uncached)} need extraction")

    new_nodes: list = []
    new_edges: list = []
    new_hyper: list = []
    tok_in = 0
    tok_out = 0
    failures: list[str] = []

    api_key = load_api_key()
    with httpx.Client() as client:
        for i, fpath in enumerate(uncached, 1):
            log(f"[{i}/{len(uncached)}] extracting: {fpath}")
            try:
                result, pt, ct = extract_one(client, api_key, Path(fpath))
                new_nodes.extend(result.get("nodes", []))
                new_edges.extend(result.get("edges", []))
                new_hyper.extend(result.get("hyperedges", []))
                tok_in += pt
                tok_out += ct
                log(f"  → {len(result.get('nodes', []))} nodes, {len(result.get('edges', []))} edges, tokens in={pt} out={ct}")
            except Exception as e:
                log(f"  FAILED: {e}")
                failures.append(fpath)

    save_semantic_cache(new_nodes, new_edges, new_hyper)
    log(f"cached {len(set(n.get('source_file', '') for n in new_nodes))} files")

    all_nodes_raw = cached_nodes + new_nodes
    all_edges_raw = cached_edges + new_edges
    all_hyper_raw = cached_hyperedges + new_hyper

    seen = set()
    deduped = []
    for n in all_nodes_raw:
        if n["id"] not in seen:
            seen.add(n["id"])
            deduped.append(n)

    fragment = {
        "nodes": deduped,
        "edges": all_edges_raw,
        "hyperedges": all_hyper_raw,
        "input_tokens": tok_in,
        "output_tokens": tok_out,
    }

    graph_path = OUT / "graph.json"
    if graph_path.exists():
        existing_data = json.loads(graph_path.read_text())
        G_existing = json_graph.node_link_graph(existing_data, edges="links")
        log(f"existing graph: {G_existing.number_of_nodes()} nodes, {G_existing.number_of_edges()} edges")
    else:
        import networkx as nx
        G_existing = nx.Graph()
        log("no existing graph — starting fresh")

    if deleted:
        to_remove = [n for n, d in G_existing.nodes(data=True) if d.get("source_file") in deleted]
        G_existing.remove_nodes_from(to_remove)
        log(f"pruned {len(to_remove)} nodes from {len(deleted)} deleted file(s)")

    G_new = build_from_json(fragment)
    G_existing.update(G_new)
    log(f"merged: {G_existing.number_of_nodes()} nodes, {G_existing.number_of_edges()} edges")

    communities = cluster(G_existing)
    cohesion = score_all(G_existing, communities)
    gods = god_nodes(G_existing)
    surprises = surprising_connections(G_existing, communities)
    labels = {cid: f"Community {cid}" for cid in communities}
    questions = suggest_questions(G_existing, communities, labels)

    total_words = 0
    for cat in incremental["new_files"].values():
        for f in cat:
            try:
                total_words += len(Path(f).read_text(errors="ignore").split())
            except Exception:
                pass
    detection = {
        "files": incremental["new_files"],
        "total_files": new_total,
        "total_words": total_words,
        "needs_graph": True,
        "warning": None,
        "skipped_sensitive": [],
    }
    tokens = {"input": tok_in, "output": tok_out}

    report = generate(G_existing, communities, cohesion, labels, gods, surprises, detection, tokens, str(CORPUS), suggested_questions=questions)
    (OUT / "GRAPH_REPORT.md").write_text(report)
    to_json(G_existing, communities, str(OUT / "graph.json"))
    if G_existing.number_of_nodes() <= 5000:
        to_html(G_existing, communities, str(OUT / "graph.html"), community_labels=labels)

    save_manifest(detection["files"])

    cost_path = OUT / "cost.json"
    cost = json.loads(cost_path.read_text()) if cost_path.exists() else {"runs": [], "total_input_tokens": 0, "total_output_tokens": 0}
    cost["runs"].append({
        "date": datetime.now(timezone.utc).isoformat(),
        "input_tokens": tok_in,
        "output_tokens": tok_out,
        "files": new_total,
        "source": "llm-update.py",
    })
    cost["total_input_tokens"] += tok_in
    cost["total_output_tokens"] += tok_out
    cost_path.write_text(json.dumps(cost, indent=2))

    log(f"tokens this run: {tok_in:,} in / {tok_out:,} out  ·  cumulative: {cost['total_input_tokens']:,}/{cost['total_output_tokens']:,}")

    if failures:
        log(f"WARN: {len(failures)} file(s) failed extraction — flag kept for retry")
        return 2

    FLAG.unlink(missing_ok=True)
    log("=== llm-update done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
