#!/command/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# Home Assistant App: TimescaleDB
# Post-backup script - Cleans up SQL dump after Home Assistant backup completes
# ==============================================================================
# shellcheck source=/dev/null
source /usr/share/timescaledb/backup_common.sh

declare DUMP_FILE

bashio::log.info "Starting post-backup cleanup..."

# Remove the backup file(s) if they exist (compressed, and legacy uncompressed)
for DUMP_FILE in "${BACKUP_FILE}" "${LEGACY_BACKUP_FILE}"; do
    if [[ -f "${DUMP_FILE}" ]]; then
        if rm -f "${DUMP_FILE}"; then
            bashio::log.info "Backup SQL file ${DUMP_FILE} removed successfully."
        else
            bashio::log.error "Failed to remove backup SQL file at ${DUMP_FILE}"
            exit 1
        fi
    else
        bashio::log.debug "No backup SQL file ${DUMP_FILE} to clean up."
    fi
done

bashio::log.info "Post-backup cleanup completed."
