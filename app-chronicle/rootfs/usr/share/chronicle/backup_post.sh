#!/command/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# Chronicle - runs after Home Assistant backed up this app: removes the dump again
# ==============================================================================
# shellcheck source=/dev/null
source /usr/share/chronicle/common.sh

if [[ -f "${BACKUP_FILE}" ]]; then
    rm -f "${BACKUP_FILE}"
    bashio::log.info "The database dump of the backup was removed."
fi
