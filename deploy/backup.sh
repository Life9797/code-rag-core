#!/usr/bin/env bash
# §38 backup: backup mode on -> pg_dump (directory format, 4 jobs) -> volumes -> plugins -> manifest -> backup mode off.
# The keys folder (JWT signing key, secrets master.key) goes to keys.tar: store it APART from the rest.
# Usage: bash deploy/backup.sh /backups/2026-10-23
set -euo pipefail
cd "$(dirname "$0")"; DEST=$(realpath -m "${1:?usage: backup.sh DEST}"); mkdir -p "$DEST"
envv() { [ -f .env ] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
D=$(envv DATA_DIR); DATA=$(realpath -m "${D:-../data}")
DC=(bash dc.sh)
"${DC[@]}" run --rm -T init python -m coderag_core.coreadm backup-mode on >/dev/null
trap '"${DC[@]}" run --rm -T init python -m coderag_core.coreadm backup-mode off >/dev/null' EXIT
"${DC[@]}" exec -T postgres sh -c 'rm -rf /tmp/db && pg_dump -U postgres -d coderag --format=directory --jobs 4 -f /tmp/db && tar -C /tmp -cf - db && rm -rf /tmp/db' | tar -x -C "$DEST"
tar -C "$DATA" -cf "$DEST/vol.tar" vol
tar -C "$DATA" -cf "$DEST/keys.tar" keys
tar -C .. -cf "$DEST/plugins.tar" plugins
{ echo "created=$(date -u +%FT%TZ)"
  echo "dump_toc_sha256=$(sha256sum "$DEST/db/toc.dat" | cut -d' ' -f1)"
  echo "vol_files=$(tar -tf "$DEST/vol.tar" | grep -vc '/$')"
  "${DC[@]}" exec -T postgres psql -U postgres -d coderag -Atc "SELECT 'pg=' || current_setting('server_version') UNION ALL SELECT 'pgvector=' || extversion FROM pg_extension WHERE extname = 'vector' UNION ALL SELECT component || '=' || version FROM core.schema_version UNION ALL SELECT 'plugin.' || id || '=' || version || ' ' || dir_hash FROM core.plugins WHERE status = 'registered'"
} > "$DEST/manifest.txt"
echo "backup written to $DEST (move keys.tar to separate storage)"
