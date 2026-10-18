#!/command/with-contenv bashio
# ==============================================================================
# Home Assistant Add-on: TimescaleDb
# Post-backup script - Cleans up SQL dump after Home Assistant backup completes
# ==============================================================================
declare BACKUP_FILE

bashio::log.info "Starting post-backup cleanup..."

# Remove the backup file(s) if they exist (compressed, and legacy uncompressed)
for BACKUP_FILE in /data/backup_db.sql.gz /data/backup_db.sql; do
    if [[ -f "${BACKUP_FILE}" ]]; then
        if rm -f "${BACKUP_FILE}"; then
            bashio::log.info "Backup SQL file ${BACKUP_FILE} removed successfully."
        else
            bashio::log.error "Failed to remove backup SQL file at ${BACKUP_FILE}"
            exit 1
        fi
    else
        bashio::log.debug "No backup SQL file ${BACKUP_FILE} to clean up."
    fi
done

bashio::log.info "Post-backup cleanup completed."
