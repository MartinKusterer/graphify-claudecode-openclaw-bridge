#!/bin/bash
# MCP stdio server for graphify — exposes graph.json as tools to Claude Code
exec /root/graphify-bridge/venv/bin/python3 -c "from graphify.serve import serve; serve('/root/graphify-bridge/graphify-out/graph.json')"
