#!/usr/bin/env sh
# Back up the production stack (production.yml) to ~/continuum-backups/<UTC stamp>/:
# every Qdrant collection (the memories) and a Postgres dump (accounts,
# decisions, rules). Run from continuum-be/, by hand or from cron:
#
#   0 3 * * *  cd /home/ubuntu/Continuum/continuum-be && sh scripts/backup-production.sh
#
# A backup holds every memory — Personal ones, health included — in plain text.
# Copy it somewhere private off the machine; restore with scripts/restore.py.
set -eu

COMPOSE="docker compose -f production.yml"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="${BACKUP_DIR:-$HOME/continuum-backups}/$STAMP"
KEEP="${BACKUP_KEEP:-14}"   # how many backups to keep on the machine
mkdir -p "$OUT"

# Postgres: pg_dump inside its own container; nothing to install on the host.
$COMPOSE exec -T postgres sh -c \
  'pg_dump --clean --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$OUT/postgres.sql"

# Qdrant is reachable only on the compose network, so the snapshot is taken from
# inside the api container (backup.py, Postgres skipped) and copied out.
$COMPOSE exec -T api sh -c 'rm -rf /tmp/qdrant-backup && \
  python scripts/backup.py --skip-postgres --out /tmp/qdrant-backup'
$COMPOSE cp api:/tmp/qdrant-backup "$OUT/qdrant"
$COMPOSE exec -T api rm -rf /tmp/qdrant-backup

# Keep the newest $KEEP backups.
ls -1d "$(dirname "$OUT")"/*/ 2>/dev/null | sort | head -n "-$KEEP" | xargs -r rm -rf

echo "backup written to $OUT"
