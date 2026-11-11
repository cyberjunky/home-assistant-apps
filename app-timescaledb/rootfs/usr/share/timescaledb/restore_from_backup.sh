#!/command/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# Home Assistant App: TimescaleDB
# Restore script - Restores the databases from the SQL dump of a Home Assistant backup.
# Called during start-up when a dump is found and the PostgreSQL data directory is
# missing (i.e. after a backup restore).
# ==============================================================================
# shellcheck source=/dev/null
source /usr/share/timescaledb/backup_common.sh

declare POSTGRES_DATA
declare RESTORE_LOG
declare RESTORE_FILE

POSTGRES_DATA="/data/postgres"
RESTORE_LOG="/var/log/timescaledb.restore.log"
# Compressed dump (5.5.0 and later); older versions wrote an uncompressed backup_db.sql
RESTORE_FILE="${BACKUP_FILE}"
if [[ ! -f "${RESTORE_FILE}" ]] && [[ -f "${LEGACY_BACKUP_FILE}" ]]; then
    RESTORE_FILE="${LEGACY_BACKUP_FILE}"
fi

# Adds the steps TimescaleDB needs around restoring a logical dump to the SQL of a plain
# pg_dumpall: timescaledb_pre_restore() right after "CREATE EXTENSION ... timescaledb" of
# a database, and timescaledb_post_restore() after its data (before the next \connect, or
# at the end). Without them TimescaleDB loses the rows of compressed chunks.
# The dump empties search_path, so the functions are called in the extension's schema.
# Lines inside COPY data are passed through untouched.
# Approach of https://github.com/expaso/hassos-addon-timescaledb/pull/91.
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

# Lists the databases that accept connections, one per line
listDatabases() {
    psql -X -tA -U postgres -d postgres -c "select datname from pg_database where datallowconn and not datistemplate"
}

# Makes sure no database is left in TimescaleDB restore mode (e.g. when the dump ended
# early), and moves the sequences up to the data (duplicate keys otherwise).
finishDatabases() {
    local database
    local schema
    while IFS= read -r database; do
        schema="$(psql -X -tA -U postgres -d "${database}" -c "select n.nspname from pg_extension e join pg_namespace n on n.oid = e.extnamespace where e.extname = 'timescaledb'")" || schema=""
        if [[ -n "${schema}" ]] \
            && [[ "$(psql -X -tA -U postgres -d "${database}" -c "select current_setting('timescaledb.restoring', true)")" == "on" ]]; then
            bashio::log.warning "Database '${database}' was still in TimescaleDB restore mode, ending it.."
            psql -X -q -U postgres -d "${database}" -c "SELECT \"${schema}\".timescaledb_post_restore();" >> "${RESTORE_LOG}" 2>&1 \
                || bashio::log.warning "Could not end the restore mode of '${database}'"
        fi
        fixSequences "${database}" >> "${RESTORE_LOG}" 2>&1 \
            || bashio::log.warning "Could not check the sequences of '${database}'"
    done < <(listDatabases)
}

# Function to restore from SQL backup
restoreFromBackup() {
    local postgres_pid
    local retry_count=0
    local restore_exit
    local -a reader

    bashio::log.notice "==================================================================="
    bashio::log.notice "  DATABASE RESTORE IN PROGRESS"
    bashio::log.notice "==================================================================="
    bashio::log.notice "A backup SQL file was found. Attempting to restore database..."

    # Verify backup file exists and is readable
    if [[ ! -f "${RESTORE_FILE}" ]]; then
        bashio::log.error "Backup file not found at ${RESTORE_FILE}"
        return 1
    fi

    if [[ ! -r "${RESTORE_FILE}" ]]; then
        bashio::log.error "Backup file is not readable at ${RESTORE_FILE}"
        return 1
    fi

    bashio::log.info "Backup file size: $(du -h "${RESTORE_FILE}" | cut -f1)"

    if [[ "${RESTORE_FILE}" == *.gz ]]; then
        reader=(gzip -dc "${RESTORE_FILE}")
    else
        reader=(cat "${RESTORE_FILE}")
    fi

    # Start postgres temporarily for restore
    bashio::log.info "Starting PostgreSQL for restore process..."
    su - postgres -c "postgres -D ${POSTGRES_DATA}" &
    postgres_pid=$!

    # Wait for postgres to become available
    bashio::log.info "Waiting for PostgreSQL to be ready..."
    while ! psql -X -U postgres postgres -c "" 2>/dev/null; do
        sleep 1
        retry_count=$((retry_count + 1))
        if [[ ${retry_count} -ge 30 ]]; then
            bashio::log.error "PostgreSQL failed to start within 30 seconds"
            kill "${postgres_pid}" 2>/dev/null || true
            return 1
        fi
    done

    bashio::log.info "PostgreSQL is ready. Starting restore..."
    mkdir -p /var/log
    : > "${RESTORE_LOG}"

    # Restore, with TimescaleDB's pre/post restore steps added per database. Errors on
    # objects that already exist in the fresh cluster (the postgres role, ..) are expected
    # and only logged; psql's exit code tells whether the dump could be read and loaded.
    restore_exit=0
    set +o errexit
    (
        set -o pipefail
        "${reader[@]}" | addTimescaleRestoreSteps | psql -X -U postgres -d postgres
    ) >> "${RESTORE_LOG}" 2>&1
    restore_exit=$?
    set -o errexit

    local errors
    errors="$(grep -c "ERROR:" "${RESTORE_LOG}" || true)"
    if [[ "${errors:-0}" -gt 0 ]]; then
        bashio::log.warning "The restore reported ${errors} error(s), see ${RESTORE_LOG}. The last ones:"
        grep "ERROR:" "${RESTORE_LOG}" | tail -5 | while IFS= read -r line; do
            bashio::log.warning "  ${line}"
        done
    fi

    if [[ "${restore_exit}" -eq 0 ]]; then
        bashio::log.info "Checking TimescaleDB restore mode and sequences..."
        finishDatabases
    fi

    # Stop postgres
    bashio::log.info "Stopping PostgreSQL..."
    kill "${postgres_pid}" 2>/dev/null || true
    wait "${postgres_pid}" || true

    if [[ "${restore_exit}" -ne 0 ]]; then
        bashio::log.error "Failed to restore database from backup!"
        bashio::log.error "Check ${RESTORE_LOG} for details"
        bashio::log.notice "==================================================================="
        bashio::log.notice "  DATABASE RESTORE FAILED"
        bashio::log.notice "==================================================================="
        return 1
    fi

    bashio::log.notice "Database restored successfully from backup!"

    # Remove the backup file after successful restore
    bashio::log.info "Removing backup file after successful restore..."
    rm -f "${BACKUP_FILE}" "${LEGACY_BACKUP_FILE}"

    bashio::log.notice "==================================================================="
    bashio::log.notice "  DATABASE RESTORE COMPLETED SUCCESSFULLY"
    bashio::log.notice "==================================================================="
    return 0
}

# Export the function so it can be called from other scripts
export -f restoreFromBackup
