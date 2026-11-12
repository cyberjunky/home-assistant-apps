#!/bin/sh
# Wi-Fi watchdog, version 3 (2026-11-12): when the router can't be reached, bring the Wi-Fi back.
# Version 2 (never rebooted): /root/backup_files/wifi-watchdog.sh.v2, version 1: .v1 next to it.
#
# cron starts jobs with PATH=/usr/bin:/bin, without ifup, ifdown, rmmod, modprobe (/sbin) and
# wpa_cli (/usr/sbin). Versions 1 and 2 threw the errors away, so none of their steps ever ran;
# the first version 3 logged "not found", and after killing wpa_supplicant couldn't start it again.
PATH=/sbin:/usr/sbin:/bin:/usr/bin
export PATH
#
# Steps, each only when the one before didn't help:
#   1. associated but no network: renew the DHCP lease
#   2. still connecting: leave wpa_supplicant alone for a while
#   3. restart wlan0 (its pre-up scripts kill and start wpa_supplicant and DHCP), at most every 10 min
#   4. reload the Wi-Fi driver (rtl8191su), at most once an hour
#   5. wpa_supplicant can't be stopped: reboot, after 20 minutes without network, at most every 6 hours
GW=${GW:-192.168.2.254}
LOG=${LOG:-/root/wifi-watchdog.log}
STATE=${STATE:-/var/run/wifi-watchdog}
REBOOTED=${REBOOTED:-/root/wifi-watchdog.rebooted}  # on flash: the 6 hour limit must survive the reboot
DRIVER=rtl8191su
DRYRUN=${DRYRUN:-0}

RESTART_EVERY=600
RELOAD_EVERY=3600
REBOOT_AFTER=1200
REBOOT_EVERY=21600

NOW=$(date +%s)
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }
reach() { ping -c 3 -W 2 "$GW" >/dev/null 2>&1; }
# Run a command; log it when it fails or says something, so a step that does nothing shows up
act() {
    if [ "$DRYRUN" = 1 ]; then log "dry run, would run: $*"; return 0; fi
    out=$("$@" 2>&1); rc=$?
    if [ $rc -ne 0 ] || [ -n "$out" ]; then log "$*: exit $rc $(echo "$out" | tr '\n' ' ' | cut -c1-200)"; fi
    return $rc
}
pause() { [ "$DRYRUN" = 1 ] || sleep "$1"; }
# Wait up to $1 seconds for the router, checking every 5
wait_reach() {
    waited=0
    while [ $waited -lt "$1" ]; do
        pause 5; waited=$((waited + 5))
        reach && return 0
    done
    return 1
}
# Seconds since the time stored in a file; a large number when there is none
since() { if [ -f "$1" ]; then echo $((NOW - $(cat "$1"))); else echo 999999; fi; }
mark() { [ "$DRYRUN" = 1 ] || echo "$NOW" > "$1"; }
back() { log "back after $1, $(( $(since "$STATE/down") / 60 )) min without network"; rm -f "$STATE/down"; exit 0; }

wpa_state() { wpa_cli -i wlan0 status 2>/dev/null | sed -n 's/^wpa_state=//p'; }
state() {
    pid=$(pidof wpa_supplicant)
    proc=""
    for p in $pid; do proc="$proc $p:$(awk '/^State:/ {print $2}' /proc/$p/status 2>/dev/null)"; done
    status=$(wpa_cli -i wlan0 status 2>/dev/null | grep -E '^(wpa_state|bssid|ip_address)=' | tr '\n' ' ')
    link=$(grep wlan0 /proc/net/wireless 2>/dev/null | awk '{print "link " $3 " level " $4}')
    echo "wpa_supplicant${proc:- not running}; ${status:-no answer}; ${link:-no link}"
}
# The last kernel lines, without the driver's hex dumps and scan noise
kernel() { dmesg | grep -vE 'wpa_ie|^0x|^ *$|addbareq|set_freq|set_mode|set_scan|pmkid' | tail -n 5 | tr '\n' '|'; }

mkdir -p "$STATE"
mkdir "$STATE/lock" 2>/dev/null || exit 0
trap 'rmdir "$STATE/lock"' EXIT

# The log is on the Toon's flash: keep it small
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 200000 ]; then
    tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

if reach; then
    [ -f "$STATE/down" ] && back "the Wi-Fi reconnected by itself"
    # One line an hour shows the Toon was running; a gap in these lines means it wasn't
    [ "$(date +%M)" -lt 5 ] && log "ok, up $(cut -d. -f1 /proc/uptime) s, $(grep MemFree /proc/meminfo | tr -s ' ')"
    exit 0
fi

[ -f "$STATE/down" ] || echo "$NOW" > "$STATE/down"
DOWN=$(since "$STATE/down")
log "router $GW not reachable ($((DOWN / 60)) min); $(state)"
WPA=$(wpa_state)

# 1. Associated with the access point, but no network: the DHCP lease
if [ "$WPA" = COMPLETED ]; then
    dhcp=$(cat /var/run/udhcpc.wlan0.pid 2>/dev/null)
    if [ -n "$dhcp" ] && kill -0 "$dhcp" 2>/dev/null; then
        act kill -USR1 "$dhcp"
    else
        act udhcpc -b -R -i wlan0 -p /var/run/udhcpc.wlan0.pid -H "$(hostname)"
    fi
    wait_reach 20 && back "renewing the DHCP lease"
    log "still not reachable after renewing the DHCP lease"
fi

# 2. Still connecting: restarting now would only start it over
case "$WPA" in
    SCANNING|AUTHENTICATING|ASSOCIATING|ASSOCIATED|4WAY_HANDSHAKE|GROUP_HANDSHAKE)
        if [ "$(since "$STATE/restarted")" -lt "$RESTART_EVERY" ]; then
            log "wpa_supplicant is $WPA, giving it time"
            exit 0
        fi ;;
    DISCONNECTED|INACTIVE)
        act wpa_cli -i wlan0 reassociate
        wait_reach 30 && back "Wi-Fi reassociate" ;;
esac

# 3. Restart wlan0, at most every 10 minutes
if [ "$(since "$STATE/restarted")" -lt "$RESTART_EVERY" ]; then
    log "wlan0 restarted less than 10 minutes ago, waiting"
    exit 0
fi
mark "$STATE/restarted"
old=$(pidof wpa_supplicant)
act ifdown wlan0
pause 3
stuck=""
for p in $old; do
    if kill -0 "$p" 2>/dev/null; then
        act kill -9 "$p"
        pause 2
        kill -0 "$p" 2>/dev/null && stuck="$stuck $p"
    fi
done
if [ -n "$stuck" ] && [ "$DRYRUN" != 1 ]; then
    log "wpa_supplicant$stuck can't be stopped; $(state)"
    log "kernel: $(kernel)"
    # Disconnect the Wi-Fi module from the Toon's internal USB bus and connect it again: that ends
    # the driver calls wpa_supplicant waits in
    usb=$(basename "$(readlink -f /sys/class/net/wlan0/device/..)" 2>/dev/null)
    if [ -n "$usb" ] && [ -e "/sys/bus/usb/drivers/usb/$usb" ]; then
        log "USB reset of the Wi-Fi module ($usb)"
        act sh -c "echo $usb > /sys/bus/usb/drivers/usb/unbind"
        pause 5
        act sh -c "echo $usb > /sys/bus/usb/drivers/usb/bind"
        pause 10
        if [ -e /sys/class/net/wlan0 ]; then
            log "after the USB reset wlan0 is back ($(basename "$(readlink /sys/class/net/wlan0/device/driver)"))"
        else
            log "after the USB reset there is no wlan0; kernel: $(kernel)"
        fi
        stuck_after=""
        for p in $stuck; do kill -0 "$p" 2>/dev/null && stuck_after="$stuck_after $p"; done
        if [ -z "$stuck_after" ]; then
            log "the USB reset ended wpa_supplicant$stuck"
            act ifdown wlan0
            act ifup wlan0
            wait_reach 60 && back "a USB reset of the Wi-Fi module"
            log "still not reachable after the USB reset; $(state)"
            exit 0
        fi
        log "wpa_supplicant$stuck_after still there after the USB reset"
    else
        log "no USB reset: the Wi-Fi module's USB device wasn't found (${usb:-wlan0 is gone})"
    fi
else
    act ifup wlan0
    wait_reach 60 && back "restarting wlan0"
    log "still not reachable after restarting wlan0; $(state)"

    # 4. Reload the driver, at most once an hour
    if [ "$(since "$STATE/reloaded")" -ge "$RELOAD_EVERY" ]; then
        mark "$STATE/reloaded"
        act ifdown wlan0
        act rmmod "$DRIVER"
        pause 3
        act modprobe "$DRIVER"
        pause 5
        act ifup wlan0
        wait_reach 60 && back "reloading the Wi-Fi driver"
        log "still not reachable after reloading the Wi-Fi driver; $(state)"
        log "kernel: $(kernel)"
    fi
    exit 0
fi

# 5. wpa_supplicant hangs in the driver: only a reboot clears that
if [ "$DOWN" -lt "$REBOOT_AFTER" ]; then
    log "will reboot when the network is still gone after $((REBOOT_AFTER / 60)) min"
elif [ "$(since "$REBOOTED")" -lt "$REBOOT_EVERY" ]; then
    log "rebooted less than 6 hours ago, not rebooting again"
else
    log "rebooting: wpa_supplicant hangs and the network has been gone for $((DOWN / 60)) min"
    mark "$REBOOTED"
    sync
    act /sbin/shutdown -r now
fi
