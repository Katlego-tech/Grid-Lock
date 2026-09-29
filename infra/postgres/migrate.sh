#!/bin/sh
# migrate.sh -- apply infra/postgres/migrations/*.sql that this database hasn't had yet.
#
# Each file runs in one transaction together with its row in schema_migrations, so a
# migration is either fully applied and recorded, or not applied at all. Files run in
# filename order (0001_, 0002_, ...) and are never edited once merged: a change to the
# schema is a new file.
#
# Runs as the `db-migrate` job in docker-compose.yml; every service that reads or writes
# the database waits for it (depends_on: condition: service_completed_successfully).
# Connection comes from the standard PG* environment variables.

set -eu

dir="${MIGRATIONS_DIR:-/migrations}"
export PGOPTIONS="${PGOPTIONS:-} -c client_min_messages=warning"

psql -v ON_ERROR_STOP=1 -q <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
SQL

found=0
for file in "$dir"/*.sql; do
  [ -e "$file" ] || continue
  found=1
  version="$(basename "$file" .sql)"
  case "$version" in
    [0-9][0-9][0-9][0-9]_*) ;;
    *) echo "!! $file: migrations are named NNNN_description.sql" >&2; exit 1 ;;
  esac

  # A failed lookup must stop the run, not read as "not applied yet".
  applied="$(printf '%s\n' "SELECT count(*) FROM schema_migrations WHERE version = :'v';" \
             | psql -tAq -v ON_ERROR_STOP=1 -v v="$version")"
  if [ "$applied" = "1" ]; then
    echo "  = $version (already applied)"
    continue
  fi

  echo "  + $version"
  { cat "$file"
    printf '\n%s\n' "INSERT INTO schema_migrations (version) VALUES (:'v');"
  } | psql -v ON_ERROR_STOP=1 -q --single-transaction -v v="$version"
done

if [ "$found" -eq 0 ]; then
  echo "!! no migrations found in $dir -- refusing to report an empty schema as migrated" >&2
  exit 1
fi
echo "migrations up to date"
