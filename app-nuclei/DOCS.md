# Home Assistant App: Nuclei Scanner

An internal vulnerability scanner with ticketing, built on nuclei and naabu.

## Setup

1. Install and start the app, then open it from the sidebar.
2. On **Scan objects**, add a target (an IP, a network like `10.0.0.0/24`, a host name, a URL, or an ASN). Review the hosts it covers and confirm you are authorised. Private ranges are treated as yours.
3. Press **Scan**, or give the object a daily, weekly or monthly schedule.
4. Findings appear under **Tickets**.

## How a scan works

1. The templates are brought up to date first (**Update templates**), so every scan has the newest CVE checks. Your own templates run alongside them.
2. **naabu** finds the open ports of each host.
3. **nuclei** then runs two passes. Its **web templates** (HTTP and TLS) run on web ports. Its **protocol templates** (SSH, SMB, FTP, Telnet, databases, TLS and more) run on the other ports, where they find things web templates cannot, such as SMB signing not being required or SSH allowing password logins. A port the app can't place gets both.
4. Services that shouldn't normally be exposed (Telnet, SMB, RDP, VNC, open databases and more) become tickets by themselves.
5. Findings at or above **Minimum severity** become tickets. Informational results aren't tickets: they are how the app recognises what a device is.

A scan is thorough rather than quick, and one host can take several minutes. A whole network can take hours. That is expected. If a scanner fails or no templates are installed, the scan is marked **failed** with the reason, and your tickets are left as they were: a scan that did not run never closes anything.

## Devices

Every host with an open port becomes a device, with its services listed (http, ssh, telnet, smb and so on) and a best-guess type from its ports and what nuclei detects. Open a device to rename it, describe it and tag it (linux, switch, camera, doorbell, tv and so on). Your edits are never overwritten by a later scan.

**Online or offline** is checked every few minutes (the **Online check** option, 0 turns it off) with a few connection attempts to each known device, and a ping when that gets no answer. A refused connection counts as online, because something replied. Only devices of objects you authorised are checked. A device a scan has just found open ports on is online. The page shows how long it has been in that state. Services that shouldn't normally be exposed, such as telnet, SMB, RDP, VNC and open databases, also become tickets.

## Tickets

Each finding is a ticket. Open it to see the proof, description, remediation, the CVE and its CVSS score, and set its status: open, in progress, false positive, won't fix, or resolved. A later scan of the same object reopens a ticket that comes back and resolves one that is gone.

## Templates

nuclei carries the whole community template set, so you get the latest CVEs without curating a list. The **Templates** tab lets you paste your own nuclei templates (YAML); each is checked by nuclei before it is saved and then runs on every scan. Keep **Update templates** on so scans use the newest checks.

## Options

Set these on the app's Configuration tab. The minimum severity, the rate limit, excluded tags (dos, fuzzing and intrusive by default), the optional default-credential check (off by default), how long resolved tickets are kept, whether templates update before a scan, and how often devices are checked for being online.

## Database

All data is one SQLite file in `/data`, in Home Assistant backups. **Settings → Database backup** downloads a copy. **Delete all data** in the danger zone empties it.
