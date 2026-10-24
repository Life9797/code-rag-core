#!/usr/bin/env bash
# docker compose with the right files for this install (pattern from v0.2.4 deploy/dc.sh):
#   + compose/llm-network.yml when LLM_NETWORK is set; + compose/webui-network.yml when WEBUI_NETWORK is set and differs.
# Usage: bash deploy/dc.sh up -d | ps | logs -f worker | run --rm api python -m coderag_core.coreadm ...
set -euo pipefail
cd "$(dirname "$0")"
envv() { [ -f .env ] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
F=(-f docker-compose.yml)
[ -n "$(envv LLM_NETWORK)" ] && F+=(-f compose/llm-network.yml)
W=$(envv WEBUI_NETWORK); [ -n "$W" ] && [ "$W" != "$(envv LLM_NETWORK)" ] && F+=(-f compose/webui-network.yml)
[ "$(envv OFFLINE)" = 1 ] && F+=(-f offline/docker-compose.offline.yml)
D=$(envv DATA_DIR); mkdir -p "${D:-../data}/vol" "${D:-../data}/keys" "${D:-../data}/db/api" "${D:-../data}/db/worker"
chown -R 1000:1000 "${D:-../data}" 2>/dev/null || echo "note: ${D:-../data} must be writable by uid 1000 (the api/worker user)"
exec docker compose "${F[@]}" "$@"
