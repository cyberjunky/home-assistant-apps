#!/usr/bin/with-contenv bashio
# ==============================================================================
# Proxmox Backup Server - let go of the datastore disks cleanly once PBS has stopped
# ==============================================================================
sync
awk '$5 ~ "^/mnt/datastore/" { print $5 }' /proc/self/mountinfo | sort -r | while read -r where; do
    umount "${where}" 2>/dev/null || umount -l "${where}"
    echo "[INFO] Unmounted ${where}"
done
