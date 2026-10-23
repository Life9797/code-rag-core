#!/usr/bin/env bash
# Recreate database ${1:-crc_dev} on the local test server and run init.
set -euo pipefail
cd "$(dirname "$0")/.."; DB=${1:-crc_dev}; BASE=$(bash scripts/testpg.sh | sed 's/dbname=postgres//')
psql "$BASE" -qc "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" 2>/dev/null
python3 -m coderag_core.initdb --dsn "$BASE dbname=$DB"
