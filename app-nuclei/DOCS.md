# Home Assistant App: Nuclei Scanner

An internal vulnerability scanner built on nuclei and naabu. It scans the devices Home Assistant tracks,
and turns what it finds into issues you work through.

## Setup

1. Install and start the app, then open it from the sidebar.
2. There is nothing to add: the devices come from Home Assistant's device trackers (from your router
   integration, the Companion app and so on). The first scans start within a minute.

## Which devices are scanned

A device tracker becomes a device in the app. A device is only ever scanned when Home Assistant reports
it as **home**, with an address on your own network: private, loopback or link-local. A tracker with a
public address, or none, is listed but never scanned.

A device is scanned:

- when it is **new**;
- when you press **Scan now** on it;
- again once it is home and its last scan is older than **Rescan after** (7 days by default). A laptop that
  was away for two weeks is scanned soon after it comes home.

One device is scanned at a time, so the network stays usable. A scan that fails is tried again after an
hour. **Never scan this device** on a device's page leaves it out for good.

## How a scan works

1. The nuclei templates are brought up to date first (**Update templates**, at most once a day), so every
   scan has the newest CVE checks. Your own templates run alongside them.
2. **naabu** finds the device's open ports.
3. Services that shouldn't normally be exposed (Telnet, SMB, RDP, VNC, open databases and more) become
   issues by themselves.
4. **nuclei** runs two passes. Its **web templates** (HTTP and TLS) run on web ports. Its **protocol
   templates** (SSH, SMB, FTP, Telnet, databases, TLS and more) run on the other ports. A port the app
   can't place gets both.
5. Findings at or above **Minimum severity** become issues.

After a complete scan, the device's issues that scan did not find again are resolved. A scan that fails
is marked **failed** with the reason and resolves nothing.

## Issues

Each issue shows the proof, a description, what to do, the CVE and its CVSS score. **Acknowledge** says
you know about it; it still counts in the score until a scan no longer finds it. An issue that comes back
after it was resolved needs attention again. Step through a list with ← and →.

## The dashboard

The **security score** (0 to 100) weighs every issue still there: a critical counts 25, a high 10, a
medium 4, a low 1, and the score halves at 50 points. Next to it the issues per severity, how many devices
were scanned within the rescan period, the trend of the last 30 days, the scan that is running and what
needs attention.

The **Scanner** page shows the installed nuclei, naabu and template versions against the newest releases,
how many checks the templates hold and the newest CVE they cover, and the recent scans.

## Your templates

On the **Scanner** page you can paste your own nuclei templates (YAML). nuclei checks each one before it
is saved; it then runs on every scan.

## Options

| Option | Default | |
|---|---|---|
| Rescan after (days) | 7 | How old the last scan of a home device may be before it is scanned again |
| Concurrent hosts | 25 | How many hosts naabu probes at once |
| Requests per second | 150 | The rate limit for naabu and nuclei together |
| Minimum severity | low | The lowest severity that becomes an issue |
| Only these tags / Exclude tags | – / dos, fuzzing, intrusive | Which nuclei templates run |
| Default-credential check | off | nuclei's default-login checks: real login attempts, which can lock accounts |
| Keep data (days) | 365 | Resolved issues and finished scans; open issues always stay |
| Update templates | on | Keep the community templates current |

## Access

The app reads Home Assistant's device trackers with the token every app gets; `homeassistant_api` in
its configuration allows that. Nothing to set up. It runs on the host network so naabu and nuclei can
reach your devices.

## Database

All data is one SQLite file in `/data`, in Home Assistant backups. **Settings → Database backup** downloads
a copy. **Delete all data** in the danger zone empties it.

## Development

`python3 -m tools.mock` runs the app with a stand-in Home Assistant (about twenty device trackers that come
and go) and a fake nuclei and naabu that answer in seconds; open http://localhost:8099. `--fresh` starts
empty. Tests: `python -m pytest tests`, lint: `./lint.sh`.
