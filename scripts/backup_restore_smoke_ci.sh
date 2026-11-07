#!/usr/bin/env bash
# End-to-end backup restoration rehearsal, using only the disposable CI service.
set -euo pipefail
db_container="$(docker ps --filter 'ancestor=postgres:17-alpine' --format '{{.ID}}' | head -n 1)"
test -n "$db_container" || { echo "Disposable Postgres container not found"; exit 1; }
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
umask 077

# Produce the same custom-format PostgreSQL dump used by the weekly workflow.
docker exec "$db_container" pg_dump -U postgres -d noor_stage -Fc --no-owner --no-acl > "$temporary/noor.dump"
test -s "$temporary/noor.dump"
export BACKUP_FERNET_KEY
BACKUP_FERNET_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
echo "::add-mask::$BACKUP_FERNET_KEY"
python scripts/backup_crypto.py encrypt "$temporary/noor.dump" "$temporary/noor.dump.enc"
python scripts/backup_crypto.py verify "$temporary/noor.dump.enc"
python scripts/backup_crypto.py decrypt "$temporary/noor.dump.enc" "$temporary/restored.dump"
cmp "$temporary/noor.dump" "$temporary/restored.dump"

# Restore into a second isolated database. No production database or project is involved.
docker exec "$db_container" createdb -U postgres noor_restore
docker exec -i "$db_container" pg_restore -U postgres -d noor_restore --no-owner --no-acl < "$temporary/restored.dump"
restored_count="$(docker exec "$db_container" psql -U postgres -d noor_restore -Atc   "SELECT count(*) FROM noor_app.v2_centers")"
test "$restored_count" = "2" || { echo "Restored center count is unexpected"; exit 1; }
rls_count="$(docker exec "$db_container" psql -U postgres -d noor_restore -Atc   "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace     WHERE n.nspname='noor_app' AND c.relkind='r' AND c.relname LIKE 'v2_%' AND c.relrowsecurity")"
test "$rls_count" -ge 25 || { echo "Restored RLS policy coverage is incomplete"; exit 1; }
echo "Disposable backup-and-restoration drill passed: 2 centers and RLS-enabled tables restored."
