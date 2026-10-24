#!/usr/bin/env bash
# Start a throwaway local PostgreSQL for tests (PG18 if PGBIN points there, else PG16 + uuidv7 shim).
# Usage: bash scripts/testpg.sh  -> prints the DSN; then: CRC_ADMIN_DSN=... pytest tests
set -euo pipefail
PGBIN=${PGBIN:-/usr/lib/postgresql/16/bin}; PGDATA=${CRC_PGDATA:-/tmp/crc-pg}; PORT=${CRC_PGPORT:-5519}
if [ ! -d "$PGDATA" ]; then
  mkdir -p "$PGDATA"; chown postgres "$PGDATA"
  su postgres -c "$PGBIN/initdb -D $PGDATA -A trust -U postgres" >/dev/null
fi
$PGBIN/pg_isready -h /tmp -p $PORT -q || su postgres -c "$PGBIN/pg_ctl -D $PGDATA -o '-p $PORT -k /tmp' -l $PGDATA/log start -w" >/dev/null
echo "host=/tmp port=$PORT user=postgres dbname=postgres"
