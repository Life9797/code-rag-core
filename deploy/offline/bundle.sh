#!/usr/bin/env bash
# Build the air-gapped bundle on a machine WITH internet (pattern from v0.2.4 deploy/offline/bundle.sh).
#   bash deploy/offline/bundle.sh [out_dir] [golden_dir]
# Output <out_dir>/code-rag-core-offline/: images.tar (postgres+pgvector, coderag-core, TEI CPU), models/bge-m3,
# repo/ (git archive), golden/ (optional, for deploy/m3eval.sh), MANIFEST, SHA256SUMS. vLLM/Open WebUI are never carried.
set -euo pipefail
OUT=$(mkdir -p "${1:-.}" && cd "${1:-.}" && pwd)/code-rag-core-offline; GOLDEN=${2:-}
cd "$(dirname "$0")/../.."
PG_IMAGE=${PG_IMAGE:-pgvector/pgvector:0.8.1-pg18}
EMBED_IMAGE=${EMBED_IMAGE:-ghcr.io/huggingface/text-embeddings-inference:cpu-1.8}
mkdir -p "$OUT/models"
echo "== images"
docker pull "$PG_IMAGE"; docker pull "$EMBED_IMAGE"
docker build -t coderag-core:0.4 -f deploy/Dockerfile .
docker save -o "$OUT/images.tar" "$PG_IMAGE" "$EMBED_IMAGE" coderag-core:0.4
echo "== bge-m3 (pinned to the commit downloaded now; TEI needs model.safetensors)"
mkdir -p "$OUT/models/bge-m3"
EMB_SHA=$(docker run --rm -i -e HF_TOKEN="${HF_TOKEN:-}" -v "$OUT/models/bge-m3":/out python:3.12-slim sh -c \
  "pip -q install --root-user-action=ignore huggingface_hub safetensors torch --extra-index-url https://download.pytorch.org/whl/cpu >/dev/null && python -" <<'PY' | tail -1
import os
from huggingface_hub import HfApi, snapshot_download
sha = HfApi().model_info("BAAI/bge-m3").sha
snapshot_download("BAAI/bge-m3", revision=sha, local_dir="/out",
                  allow_patterns=["*.json", "*.safetensors", "pytorch_model.bin", "*.txt", "*.model", "tokenizer*", "sentencepiece*", "1_Pooling/*", "onnx/*"])
if not any(f.endswith(".safetensors") for f in os.listdir("/out")) and os.path.exists("/out/pytorch_model.bin"):
    import torch
    from safetensors.torch import save_file
    sd = torch.load("/out/pytorch_model.bin", map_location="cpu", weights_only=True)
    save_file({k: v.contiguous().clone() for k, v in sd.items()}, "/out/model.safetensors", metadata={"format": "pt"})
    os.remove("/out/pytorch_model.bin")
print(sha)
PY
)
echo "== code"
rm -rf "$OUT/repo"; mkdir -p "$OUT/repo"; git archive HEAD | tar -x -C "$OUT/repo"
[ -n "$GOLDEN" ] && { rm -rf "$OUT/golden"; cp -r "$GOLDEN" "$OUT/golden"; }
cat > "$OUT/MANIFEST" <<M
built_at=$(date -u +%FT%TZ)
repo_commit=$(git rev-parse HEAD)
embed_revision=$EMB_SHA
images=$PG_IMAGE $EMBED_IMAGE coderag-core:0.4
M
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
echo "bundle ready: $OUT  (on the GPU host: cd code-rag-core-offline && bash repo/deploy/offline/install.sh)"
