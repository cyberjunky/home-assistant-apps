#!/command/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# Chronicle - runs before Home Assistant backs up this app: a consistent dump of the whole
# cluster (pg_dumpall, gzip compressed) in /data. The data folder itself is left out of the
# backup (backup_exclude): copying the files of a running PostgreSQL gives a copy that may
# not start. After restoring the backup, the next start loads this dump (init-postgres).
#
# A watchdog stops the dump when the free space on /data drops below BACKUP_MIN_FREE_MB, and
# a failed or stopped dump is always removed, so the backup never fills the disk.
# From the TimescaleDB app of this repository.
# ==============================================================================
# shellcheck source=/dev/null
source /usr/share/chronicle/common.sh

declare DUMP_PID=""

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
    rm -f "${BACKUP_FILE}"
    bashio::log.error "The database dump was removed again; this backup does not contain the database."
    exit 1
}

# Stopped by the Supervisor (e.g. a backup timeout): never leave a partial dump behind
trap 'fail "The backup was interrupted."' INT TERM

if ! asPostgres pg_isready -q -h "${PGSOCKET}"; then
    bashio::log.warning "PostgreSQL is not running; this backup does not contain the database."
    exit 0
fi

bashio::log.info "Dumping the database for the backup.."
rm -f "${BACKUP_FILE}"
if [[ $(freeSpaceMb) -lt ${BACKUP_MIN_FREE_MB} ]]; then
    fail "Only $(freeSpaceMb) MiB free on /data, the dump needs to leave at least ${BACKUP_MIN_FREE_MB} MiB free. Free up disk space and try again."
fi

# An empty file with tight permissions first
(umask 077 && : > "${BACKUP_FILE}")

# In its own process group (setsid), so the watchdog can stop both pg_dumpall and gzip.
# ${1} and ${2} are expanded by the inner shell.
# shellcheck disable=SC2016
# PGOPTIONS: the superuser's own session settings (common.sh), whatever a database's owner set
setsid bash -c 'set -o pipefail; PGOPTIONS="${3}" s6-setuidgid postgres pg_dumpall -h "${2}" -U postgres --clean --if-exists | gzip -1 > "${1}"' \
    _ "${BACKUP_FILE}" "${PGSOCKET}" "${SUPERUSER_OPTIONS}" &
DUMP_PID=$!

# Watchdog: stop the dump when the disk gets too full
while kill -0 "${DUMP_PID}" 2>/dev/null; do
    if [[ $(freeSpaceMb) -lt ${BACKUP_MIN_FREE_MB} ]]; then
        fail "Not enough free disk space: stopped the dump to keep at least ${BACKUP_MIN_FREE_MB} MiB free for PostgreSQL."
    fi
    sleep 1
done

if ! wait "${DUMP_PID}"; then
    DUMP_PID=""
    fail "The database dump failed."
fi
DUMP_PID=""

bashio::log.info "The database dump is ready ($(du -h "${BACKUP_FILE}" | cut -f1))."
