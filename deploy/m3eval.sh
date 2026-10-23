#!/usr/bin/env bash
# M3 comparison with v0.2.4 (§35 pass rule): index the golden repos with the real bge-m3 + LLM in a separate database
# (coderag_eval, never the production one), run every query 3 times, print mean, spread and the baseline compare.
#   bash deploy/m3eval.sh GOLDEN_DIR [repeats]      results also in DATA_DIR/vol/eval/m3-<time>.json
set -euo pipefail
cd "$(dirname "$0")"; G=$(realpath "${1:?usage: m3eval.sh GOLDEN_DIR [repeats]}")
envv() { [ -f .env ] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
T=$(date -u +%Y%m%d-%H%M)
bash dc.sh run --rm -T -v "$G":/golden:ro init sh -c "mkdir -p /data/vol/eval && python -m scripts.golden_eval \
  --dsn 'host=postgres port=5432 user=postgres password=$(envv POSTGRES_PASSWORD) dbname=coderag_eval' \
  --golden /golden --vol /data/vol/eval/$T --repeats ${2:-3} --out /data/vol/eval/m3-$T.json"
