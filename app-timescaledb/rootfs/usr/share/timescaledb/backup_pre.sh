#!/command/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# Home Assistant App: TimescaleDB
# Pre-backup script - creates a compressed SQL dump before the Home Assistant backup runs.
# The data directory itself is excluded from the backup (backup_exclude), so this dump
# is what a restore brings back.
#
# A watchdog stops the dump when the free space on /data drops below
# BACKUP_MIN_FREE_MB, and a failed or stopped dump is always removed, so the backup can
# never fill the disk and take PostgreSQL down (approach of
# https://github.com/expaso/hassos-addon-timescaledb/pull/91, for issue #66 there).
# ==============================================================================
# shellcheck source=/dev/null
source /usr/share/timescaledb/backup_common.sh

declare DUMP_PID=""

removeDump() {
    rm -f "${BACKUP_FILE}" "${LEGACY_BACKUP_FILE}"
}

# Stops a running dump (pg_dumpall and gzip: they run in their own process group)
stopDump() {
    if [[ -n "${DUMP_PID}" ]]; then
        kill -TERM -- "-${DUMP_PID}" 2>/dev/null || kill -TERM "${DUMP_PID}" 2>/dev/null || true
        wait "${DUMP_PID}" 2>/dev/null || true
        DUMP_PID=""
    fi
}

fail() {
    bashio::log.error "${1}"
    stopDump
    removeDump
    bashio::log.error "The database dump was removed again; this backup does not contain the database."
    exit 1
}

# Stopped by the Supervisor (e.g. a backup timeout): never leave a partial dump behind
trap 'fail "The backup was interrupted."' INT TERM

bashio::log.info "Starting pre-backup process..."

if ! pg_isready -U postgres -h localhost -p 5432 >/dev/null 2>&1; then
    bashio::log.warning "PostgreSQL is not running. Skipping database dump."
    bashio::log.warning "Note: Only file-level backup will be performed."
    exit 0
fi

bashio::log.info "PostgreSQL is running, creating database dump..."

# Leftovers of an earlier, interrupted backup
removeDump

if [[ $(backupFreeSpaceMb) -lt ${BACKUP_MIN_FREE_MB} ]]; then
    fail "Only $(backupFreeSpaceMb) MiB free on /data, the dump needs to leave at least ${BACKUP_MIN_FREE_MB} MiB free. Free up disk space and try again."
fi

# Create an empty file with tight permissions first
touch "${BACKUP_FILE}"
chmod 600 "${BACKUP_FILE}"

# The dump is streamed through gzip, so it takes a fraction of the disk space of a plain
# dump. It runs in its own process group (setsid), so the watchdog can stop both
# pg_dumpall and gzip. ${1} is expanded by the inner shell.
# shellcheck disable=SC2016
setsid bash -c 'set -o pipefail; pg_dumpall -U postgres --clean --if-exists | gzip -1 > "${1}"' _ "${BACKUP_FILE}" &
DUMP_PID=$!

# Watchdog: stop the dump when the disk gets too full
while kill -0 "${DUMP_PID}" 2>/dev/null; do
    if [[ $(backupFreeSpaceMb) -lt ${BACKUP_MIN_FREE_MB} ]]; then
        fail "Not enough free disk space: stopped the dump to keep at least ${BACKUP_MIN_FREE_MB} MiB free for PostgreSQL."
    fi
    sleep 1
done

if ! wait "${DUMP_PID}"; then
    DUMP_PID=""
    fail "Failed to create database dump!"
fi
DUMP_PID=""

chown postgres:postgres "${BACKUP_FILE}"
chmod 600 "${BACKUP_FILE}"

bashio::log.info "Database dump created successfully at ${BACKUP_FILE}"
bashio::log.info "Backup file size: $(du -h "${BACKUP_FILE}" | cut -f1)"
bashio::log.info "Pre-backup process completed."
