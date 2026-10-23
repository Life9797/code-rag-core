#!/usr/bin/env bash
# §38 restore drill (BR-01) on a fresh install (empty DB volume, empty DATA_DIR): roles -> pg_restore -> volumes ->
# keys (optional) -> init (checks plugin folders) -> api/worker -> coreadm verify.
# Usage: bash deploy/restore.sh /backups/2026-10-23 [/separate/keys.tar]
set -euo pipefail
cd "$(dirname "$0")"; SRC=$(realpath "${1:?usage: restore.sh BACKUP [KEYS_TAR]}"); KEYS=${2:-$SRC/keys.tar}
envv() { [ -f .env ] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
D=$(envv DATA_DIR); DATA=$(realpath -m "${D:-../data}")
DC=(bash dc.sh)
[ -z "$(ls -A "$DATA/vol" 2>/dev/null)" ] || { echo "DATA_DIR/vol is not empty: restore needs a fresh install"; exit 1; }
"${DC[@]}" up -d --wait postgres
"${DC[@]}" run --rm -T init python -m coderag_core init --roles-only
tar -C "$SRC" -cf - db | "${DC[@]}" exec -T postgres sh -c 'rm -rf /tmp/db && tar -C /tmp -xf - && pg_restore -U postgres -d coderag --jobs 4 /tmp/db && rm -rf /tmp/db'
tar -C "$DATA" -xf "$SRC/vol.tar"
if [ -f "$KEYS" ]; then tar -C "$DATA" -xf "$KEYS"; else echo "no keys.tar: run coreadm rotate-key /data/keys; secrets must be entered again (listed by verify)"; fi
chown -R 1000:1000 "$DATA" 2>/dev/null || true
"${DC[@]}" up -d
"${DC[@]}" run --rm -T init python -m coderag_core.coreadm verify
