# Toon tweaks

Changes made on the rooted Toon itself, not in this app, so that its Wi-Fi stays connected and comes back by itself after a drop. They are optional: the app works without them.

| File | On the Toon | What it does |
|---|---|---|
| [wifi-watchdog.sh](wifi-watchdog.sh) | `/root/wifi-watchdog.sh` | Every 5 minutes: checks that the router answers, and brings the Wi-Fi back step by step when it doesn't |
| [crontab](crontab) | root's crontab | Runs the watchdog every 5 minutes, in place of the daily reboot |
| [rtl8191su.conf](rtl8191su.conf) | `/etc/modprobe.d/rtl8191su.conf` | Wi-Fi driver options: 20 MHz channel width and no A-MPDU aggregation |

These were tested on a Toon 1 (`eneco-001`, kernel 2.6.36) with its built-in Wi-Fi module: a Realtek RTL8191SU on the Toon's internal USB bus (`13d3:3309`, driver `rtl8191su`). Check that yours uses the same driver first:

```sh
basename $(readlink /sys/class/net/wlan0/device/driver/module)   # should print rtl8191su
```

## Why

The Toon's Wi-Fi sometimes drops and doesn't come back. During one outage wpa_supplicant (the Wi-Fi client) hung inside the driver for 7 hours. Restarting wlan0 and reloading the driver did nothing, and even `kill -9` couldn't end it. Only a reboot cleared it.

Toons are often rebooted daily for this reason. The watchdog replaces that with a check every 5 minutes, which reboots only when nothing else helps.

## The watchdog

The watchdog pings the router. When the router answers, it does nothing, apart from one "ok" line an hour in the log: a gap in those lines means the Toon wasn't running. When the router doesn't answer, it tries these steps in order, and moves on to the next only when the one before didn't help:

1. **Associated with the access point but no network:** it renews the DHCP lease.
2. **Still connecting:** it leaves wpa_supplicant alone, because a restart would only start it over.
3. **Restart wlan0:** this stops and starts wpa_supplicant and DHCP, at most every 10 minutes.
4. **Reload the Wi-Fi driver:** at most once an hour.
5. **wpa_supplicant can't be stopped (it hangs in the driver):** it disconnects the Wi-Fi module from the internal USB bus and connects it again. If wpa_supplicant still hangs, it reboots, but only after 20 minutes without network and at most once every 6 hours.

Everything goes to `/root/wifi-watchdog.log`, which is kept under 200 kB. That includes every command that fails or prints something, the state of wpa_supplicant, the signal, and how the network came back ("back after restarting wlan0, 12 min without network"). Its settings are at the top of the script: the router address `GW` (default `192.168.2.254`) and the time limits.

## Installing

Copy the files to the Toon as root, over SSH. The Toon only speaks old SSH algorithms, and may have no `scp`, so this pipes each file through `cat`:

```sh
for f in wifi-watchdog.sh rtl8191su.conf crontab; do
  ssh -o HostKeyAlgorithms=+ssh-rsa -o KexAlgorithms=+diffie-hellman-group14-sha1 root@TOON "cat > /tmp/$f" < $f
done
```

Then, on the Toon:

```sh
# The watchdog: set GW in it to your router's address first
cp /tmp/wifi-watchdog.sh /root/ && chmod +x /root/wifi-watchdog.sh

# Try it without changing anything: a dry run against an address that doesn't answer
DRYRUN=1 GW=192.0.2.1 STATE=/tmp/wwt LOG=/tmp/wwt.log REBOOTED=/tmp/wwt.rb /root/wifi-watchdog.sh; cat /tmp/wwt.log

# Run it every 5 minutes; keep a copy of the current crontab
crontab -l > /root/crontab.before 2>/dev/null; crontab /tmp/crontab && crontab -l

# The driver options: they take effect after a reboot, or right away with a driver reload
mkdir -p /etc/modprobe.d && cp /tmp/rtl8191su.conf /etc/modprobe.d/
cat /sys/module/rtl8191su/parameters/cbw40_enable /sys/module/rtl8191su/parameters/ampdu_enable   # 0 and 0 once active
```

The [crontab](crontab) file replaces the whole crontab. If yours has other lines, add only the watchdog line with `crontab -e`.

To apply the driver options without rebooting, run the reload detached: the Wi-Fi goes away for about a minute, and the SSH session with it.

```sh
nohup sh -c 'sleep 3; ifdown wlan0; rmmod rtl8191su; sleep 3; modprobe rtl8191su; sleep 3; ifup wlan0' >/dev/null 2>&1 </dev/null &
```

## Reading the log

```sh
tail -n 50 /root/wifi-watchdog.log
grep -E "not reachable \(|back after|rebooting|USB reset" /root/wifi-watchdog.log
```

| Line | Meaning |
|---|---|
| `ok, up … s` | Hourly sign of life; a lower uptime than the line before means the Toon restarted |
| `router … not reachable (N min); wpa_supplicant 1234:S; wpa_state=…` | An outage; `:S` is a normal process, `:D` one stuck in the driver |
| `back after …` | Which step brought the network back, and how long it was gone |
| `wpa_supplicant … can't be stopped` | The driver hang: only the USB reset or a reboot helps |
| `USB reset of the Wi-Fi module`, `after the USB reset …`, `the USB reset ended wpa_supplicant` | Whether the USB reset worked |
| `rebooting: …` | The last step was taken |

## Undoing it

```sh
crontab /root/crontab.before                # the old crontab (or crontab -r for none)
rm /root/wifi-watchdog.sh /root/wifi-watchdog.log /root/wifi-watchdog.rebooted
rm /etc/modprobe.d/rtl8191su.conf           # driver defaults again after a reboot
```

## Wi-Fi networks the Toon can join

The Toon's Wi-Fi only supports 2.4 GHz. Choose plain WPA2-PSK with AES (CCMP) on channels 1 to 11. It won't join networks that:

- use WPA3, WPA2/WPA3 mixed mode, or PMF (802.11w) set to "required"
- use channel 12 or 13
- run 802.11ax-only
