#!/command/with-contenv bashio
# ==============================================================================
# Home Assistant Add-on: TimescaleDb
# Pre-backup script - Creates a compressed SQL dump before Home Assistant backup runs
# ==============================================================================
declare BACKUP_FILE
declare LEGACY_BACKUP_FILE

BACKUP_FILE="/data/backup_db.sql.gz"
LEGACY_BACKUP_FILE="/data/backup_db.sql"

bashio::log.info "Starting pre-backup process..."

# Check if postgres is running by trying to connect
if pg_isready -U postgres -h localhost -p 5432 >/dev/null 2>&1; then
    bashio::log.info "PostgreSQL is running, creating database dump..."

    # Remove old backup files if they exist
    rm -f "${BACKUP_FILE}" "${LEGACY_BACKUP_FILE}"

    # Create an empty file with proper ownership first
    touch "${BACKUP_FILE}"
    chown postgres:postgres "${BACKUP_FILE}"
    chmod 600 "${BACKUP_FILE}"

    # Create the SQL dump. The dump is streamed through gzip, so it takes a fraction of the
    # disk space of a plain dump (see GitHub issue #66).
    if su - postgres -c "set -o pipefail; pg_dumpall -U postgres --clean --if-exists | gzip -1 > ${BACKUP_FILE}"; then
        bashio::log.info "Database dump created successfully at ${BACKUP_FILE}"

        # Set proper permissions
        chmod 600 "${BACKUP_FILE}"
        chown postgres:postgres "${BACKUP_FILE}"

        # Log file size for verification
        BACKUP_SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
        bashio::log.info "Backup file size: ${BACKUP_SIZE}"
    else
        bashio::log.error "Failed to create database dump!"
        rm -f "${BACKUP_FILE}"
        exit 1
    fi
else
    bashio::log.warning "PostgreSQL is not running. Skipping database dump."
    bashio::log.warning "Note: Only file-level backup will be performed."
fi

bashio::log.info "Pre-backup process completed."
