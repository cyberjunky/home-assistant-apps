#!/command/with-contenv bashio
# ==============================================================================
# Home Assistant Add-on: TimescaleDb
# Restore script - Restores database from SQL dump
# ==============================================================================
declare BACKUP_FILE
declare POSTGRES_DATA

# Compressed dump (add-on 6.0.0 and later); older versions wrote an uncompressed backup_db.sql
BACKUP_FILE="/data/backup_db.sql.gz"
if [[ ! -f "${BACKUP_FILE}" ]] && [[ -f "/data/backup_db.sql" ]]; then
    BACKUP_FILE="/data/backup_db.sql"
fi
POSTGRES_DATA="/data/postgres"

# Function to restore from SQL backup
restoreFromBackup() {
    bashio::log.notice "==================================================================="
    bashio::log.notice "  DATABASE RESTORE IN PROGRESS"
    bashio::log.notice "==================================================================="
    bashio::log.notice "A backup SQL file was found. Attempting to restore database..."

    # Verify backup file exists and is readable
    if [[ ! -f "${BACKUP_FILE}" ]]; then
        bashio::log.error "Backup file not found at ${BACKUP_FILE}"
        return 1
    fi
    
    if [[ ! -r "${BACKUP_FILE}" ]]; then
        bashio::log.error "Backup file is not readable at ${BACKUP_FILE}"
        return 1
    fi

    # Ensure the backup file is accessible by the postgres user
    bashio::log.info "Fixing permissions on backup file..."
    if ! chown postgres:postgres "${BACKUP_FILE}"; then
        bashio::log.error "Could not change owner of backup file to postgres:postgres"
        return 1
    fi
    if ! chmod 640 "${BACKUP_FILE}"; then
        bashio::log.error "Could not change permissions of backup file"
        return 1
    fi
    
    # Log backup file info
    BACKUP_SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
    bashio::log.info "Backup file size: ${BACKUP_SIZE}"
    
    # Start postgres temporarily for restore
    bashio::log.info "Starting PostgreSQL for restore process..."
    su - postgres -c "postgres -D ${POSTGRES_DATA}" &
    POSTGRES_PID=$!
    
    # Wait for postgres to become available
    bashio::log.info "Waiting for PostgreSQL to be ready..."
    RETRY_COUNT=0
    MAX_RETRIES=30
    while ! psql -U postgres postgres -c "" 2>/dev/null; do
        sleep 1
        RETRY_COUNT=$((RETRY_COUNT + 1))
        if [[ ${RETRY_COUNT} -ge ${MAX_RETRIES} ]]; then
            bashio::log.error "PostgreSQL failed to start within ${MAX_RETRIES} seconds"
            kill "${POSTGRES_PID}" 2>/dev/null || true
            return 1
        fi
    done
    
    bashio::log.info "PostgreSQL is ready. Starting restore..."
    
    # Ensure the log directory exists (defensive: base image should provide it, but guard anyway)
    mkdir -p /var/log

    # Restore the backup – capture psql's exit code via PIPESTATUS, not tee's
    if [[ "${BACKUP_FILE}" == *.gz ]]; then
        su - postgres -c "set -o pipefail; gunzip -c ${BACKUP_FILE} | psql -X -U postgres -d postgres" 2>&1 | tee /var/log/timescaledb.restore.log
    else
        su - postgres -c "psql -X -U postgres -f ${BACKUP_FILE} -d postgres" 2>&1 | tee /var/log/timescaledb.restore.log
    fi
    RESTORE_EXIT=${PIPESTATUS[0]}

    if [[ "${RESTORE_EXIT}" -eq 0 ]]; then
        bashio::log.notice "Database restored successfully from backup!"
        
        # Stop postgres
        bashio::log.info "Stopping PostgreSQL..."
        kill "${POSTGRES_PID}"
        wait "${POSTGRES_PID}" || true
        
        # Remove the backup file after successful restore
        bashio::log.info "Removing backup file after successful restore..."
        rm -f "${BACKUP_FILE}"
        
        bashio::log.notice "==================================================================="
        bashio::log.notice "  DATABASE RESTORE COMPLETED SUCCESSFULLY"
        bashio::log.notice "==================================================================="
        return 0
    else
        bashio::log.error "Failed to restore database from backup!"
        bashio::log.error "Check /var/log/timescaledb.restore.log for details"
        
        # Stop postgres
        kill "${POSTGRES_PID}" 2>/dev/null || true
        wait "${POSTGRES_PID}" 2>/dev/null || true
        
        bashio::log.notice "==================================================================="
        bashio::log.notice "  DATABASE RESTORE FAILED"
        bashio::log.notice "==================================================================="
        return 1
    fi
}

# Export the function so it can be called from other scripts
export -f restoreFromBackup
