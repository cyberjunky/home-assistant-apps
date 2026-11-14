#!/command/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# Chronicle - settings and helpers shared by the database scripts.
# The TimescaleDB restore steps and fixSequences come from the TimescaleDB app of this
# repository (approach of https://github.com/expaso/hassos-addon-timescaledb/pull/91).
# ==============================================================================

# shellcheck disable=SC2034
PGDATA=/data/postgres
# shellcheck disable=SC2034
PGSOCKET=/run/postgresql
# shellcheck disable=SC2034
PGBIN=/usr/local/bin
# Where the PostgreSQL of an older major version lives, when an image carries one to upgrade
# from (see upgradeCluster): /usr/local/postgresql<version>/bin
# The passwords the app generates (one file per role), readable by the web UI's group
# shellcheck disable=SC2034
SECRETS=/data/secrets
# The web UI's own files (the donate card state, ..)
# shellcheck disable=SC2034
UI_DATA=/data/ui
# The database helper's socket folder (src/helper.py), and the backup folder
# shellcheck disable=SC2034
HELPER_RUN=/run/chronicle
# The helper's own folder: the record of the dumps it made (with their SHA-256), work files
# shellcheck disable=SC2034
HELPER_DATA=/data/helper
# shellcheck disable=SC2034
BACKUP_DIR=/share/chronicle
# The database the Chronicle integration records into
# shellcheck disable=SC2034
DATABASE=homeassistant
# Set while a PostgreSQL upgrade runs; holds the old major version (see upgradeCluster)
# shellcheck disable=SC2034
UPGRADE_MARKER=/data/postgres-upgrade

# The database dump of a Home Assistant backup: one plain pg_dumpall, gzip compressed.
# backup_pre.sh writes it, the data folder itself is left out of the backup (backup_exclude),
# and the next start after restoring the backup loads it into a new cluster.
# shellcheck disable=SC2034
BACKUP_FILE=/data/backup_db.sql.gz

# Free space (MiB) the dump always leaves on /data, so PostgreSQL never runs out of disk.
# BACKUP_MIN_FREE_MB in the environment overrides it (used for testing).
# shellcheck disable=SC2034
BACKUP_MIN_FREE_MB="${BACKUP_MIN_FREE_MB:-1024}"

# Reads an app option from the options file the Supervisor writes (bashio::config asks the
# Supervisor API instead, which the scripts don't need for two plain values)
# $1: option, $2: default when it is not set
option() {
    local value
    value="$(jq -r --arg key "${1}" '.[$key] // empty' /data/options.json 2>/dev/null || true)"
    echo "${value:-${2}}"
}

# Settings for every superuser session, sent when it connects. They take precedence over what
# a database's owner set with ALTER DATABASE ... SET: an owner could otherwise put a schema of
# its own first in search_path, and have the superuser run its functions and operators, or make
# the superuser's session read-only and stop the app from starting. Used by psql, pg_dump,
# pg_restore, vacuumdb and pg_upgrade alike (they all read PGOPTIONS).
SUPERUSER_OPTIONS="-c search_path=pg_catalog,pg_temp -c default_transaction_read_only=off -c statement_timeout=0 -c lock_timeout=0 -c idle_in_transaction_session_timeout=0 -c pg_stat_statements.track_utility=off"

# Runs a command as the postgres user (with the superuser's session settings)
asPostgres() {
    PGOPTIONS="${SUPERUSER_OPTIONS}" s6-setuidgid postgres "$@"
}

# psql as the superuser over the local socket (peer authentication)
# $@: psql arguments
psqlAdmin() {
    asPostgres psql -X -h "${PGSOCKET}" -U postgres -v ON_ERROR_STOP=1 "$@"
}

# Free space on the /data file system in MiB
freeSpaceMb() {
    df -Pm /data | awk 'NR==2 {print $4}'
}

# Lists the databases that accept connections, one per line
listDatabases() {
    psqlAdmin -tA -d postgres -c "SELECT datname FROM pg_database WHERE datallowconn AND NOT datistemplate ORDER BY 1"
}

# Starts PostgreSQL for maintenance only: on the local socket, without network and without
# TimescaleDB's background jobs, so nothing else is connected while the start-up work runs.
# $1: (optional) the bin directory of the PostgreSQL to start (default: this image's)
# $2: (optional) the data directory (default: PGDATA)
startPrivate() {
    local bindir="${1:-${PGBIN}}"
    local datadir="${2:-${PGDATA}}"
    : > /tmp/postgres-private.log
    chown postgres:postgres /tmp/postgres-private.log
    if ! asPostgres "${bindir}/pg_ctl" -D "${datadir}" -w -t 600 -l /tmp/postgres-private.log \
        -o "-c listen_addresses='' -c unix_socket_directories='${PGSOCKET}' -c timescaledb.max_background_workers=0" \
        start > /dev/null; then
        bashio::log.error "PostgreSQL did not start:"
        tail -n 20 /tmp/postgres-private.log >&2
        return 1
    fi
}

# Stops the maintenance server again
# $1, $2: as startPrivate
stopPrivate() {
    local bindir="${1:-${PGBIN}}"
    local datadir="${2:-${PGDATA}}"
    asPostgres "${bindir}/pg_ctl" -D "${datadir}" -w -t 600 -m fast stop > /dev/null
}

# Updates TimescaleDB to the release of this image in every database that has it.
# ALTER EXTENSION must be the first statement of its session, hence one psql per database.
updateTimescaleDB() {
    local database
    local installed
    local available
    while IFS= read -r database; do
        installed="$(psqlAdmin -tA -d "${database}" -c "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")"
        [[ -z "${installed}" ]] && continue
        available="$(psqlAdmin -tA -d "${database}" -c "SELECT default_version FROM pg_available_extensions WHERE name = 'timescaledb'")"
        if [[ "${installed}" != "${available}" ]]; then
            bashio::log.info "Updating TimescaleDB in database '${database}' from ${installed} to ${available}.."
            psqlAdmin -q -d "${database}" -c "ALTER EXTENSION timescaledb UPDATE"
        fi
    done < <(listDatabases)
}

# Moves every column sequence (serial and identity) of a database up to at least the
# highest value in its column. Sequences that are already ahead are left alone.
# A sequence behind its table makes every insert fail with a duplicate key
# (https://github.com/expaso/hassos-addon-timescaledb/issues/85).
# $1: database
fixSequences() {
    psqlAdmin -q -d "${1}" <<'SQL'
DO $$
DECLARE
  r record;
  max_value bigint;
BEGIN
  FOR r IN
    SELECT s.oid::regclass AS seq, t.oid::regclass AS tbl, a.attname AS col
    FROM pg_class s
    JOIN pg_depend d ON d.classid = 'pg_class'::regclass AND d.objid = s.oid
                    AND d.refclassid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')
    JOIN pg_class t ON t.oid = d.refobjid
    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
    WHERE s.relkind = 'S'
  LOOP
    EXECUTE format('SELECT max(%I) FROM %s', r.col, r.tbl) INTO max_value;
    IF max_value IS NOT NULL THEN
      EXECUTE format('SELECT setval(%L, greatest(%s, (SELECT last_value FROM %s)))', r.seq, max_value, r.seq);
    END IF;
  END LOOP;
END $$;
SQL
}

# Adds the steps TimescaleDB needs around restoring a logical dump to the SQL of a plain
# pg_dumpall: timescaledb_pre_restore() right after "CREATE EXTENSION ... timescaledb" of
# a database, and timescaledb_post_restore() after its data (before the next \connect, or
# at the end). Without them TimescaleDB loses the rows of compressed chunks.
# The dump empties search_path, so the functions are called in the extension's schema.
# Lines inside COPY data are passed through untouched.
addTimescaleRestoreSteps() {
    awk '
        incopy { print; if ($0 == "\\.") incopy = 0; next }
        /^COPY .* FROM stdin;$/ { print; incopy = 1; next }
        /^\\connect / { if (schema != "") print "SELECT " schema ".timescaledb_post_restore();"; schema = "" }
        { print }
        /^CREATE EXTENSION IF NOT EXISTS timescaledb / {
            schema = "public"
            if (match($0, /WITH SCHEMA [^ ;]+/)) schema = substr($0, RSTART + 12, RLENGTH - 12)
            print "SELECT " schema ".timescaledb_pre_restore();"
        }
        END { if (schema != "") print "SELECT " schema ".timescaledb_post_restore();" }
    '
}

# Makes sure no database is left in TimescaleDB restore mode (e.g. when a dump ended
# early), and moves the sequences up to the data.
finishRestoredDatabases() {
    local database
    local schema
    while IFS= read -r database; do
        schema="$(psqlAdmin -tA -d "${database}" -c "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'timescaledb'")" || schema=""
        if [[ -n "${schema}" ]] \
            && [[ "$(psqlAdmin -tA -d "${database}" -c "SELECT current_setting('timescaledb.restoring', true)")" == "on" ]]; then
            bashio::log.warning "Database '${database}' was still in TimescaleDB restore mode, ending it.."
            psqlAdmin -q -d "${database}" -c "SELECT \"${schema}\".timescaledb_post_restore()" \
                || bashio::log.warning "Could not end the restore mode of '${database}'"
        fi
        fixSequences "${database}" || bashio::log.warning "Could not check the sequences of '${database}'"
    done < <(listDatabases)
}
