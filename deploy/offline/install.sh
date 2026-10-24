#!/usr/bin/env bash
# Air-gapped side, from inside the bundle folder: bash repo/deploy/offline/install.sh
# Verifies checksums, loads images, writes repo/deploy/.env once (random passwords, OFFLINE=1, models from the bundle),
# then starts the stack. Edit LLM_* / networks in repo/deploy/.env if your vLLM differs from the defaults.
set -euo pipefail
B=$(cd "$(dirname "$0")/../../.." && pwd); cd "$B"
[ -f SHA256SUMS ] && [ -f MANIFEST ] || { echo "run from inside the bundle: cd code-rag-core-offline && bash repo/deploy/offline/install.sh" >&2; exit 1; }
echo "== verify"; sha256sum --quiet -c SHA256SUMS
if [ "${SKIP_LOAD:-0}" = 1 ]; then echo "== load images: skipped"; else echo "== load images"; docker load -i images.tar; fi
EMB=$(grep '^embed_revision=' MANIFEST | cut -d= -f2)
ENV=repo/deploy/.env
if [ ! -f "$ENV" ]; then
  cp repo/deploy/.env.example "$ENV"
  rnd() { head -c 18 /dev/urandom | base64 | tr -d '/+='; }
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rnd)|; 
          s|^MODELS_DIR=.*|MODELS_DIR=$B/models|; s|^EMBED_MODEL_ID=.*|EMBED_MODEL_ID=bge-m3@$EMB|" "$ENV"
  echo "OFFLINE=1" >> "$ENV"
fi
bash repo/deploy/dc.sh up -d
bash repo/deploy/dc.sh ps
