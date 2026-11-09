# Changelog

## 0.1.1

**After updating:** nothing to do.

- README redesigned to match the other apps: badges, icon, installation steps and a support section.
- The donation card (same one used by the other apps) now also appears here once in a while.

## 0.1.0

First version.

- Internal vulnerability scanner built on nuclei (CVEs and exposures) and naabu (open ports). Web templates run on web ports and protocol templates on the others, so SSH, SMB and similar services are really tested.
- Scan objects with an ownership gate: a target is scanned only after you confirm you are authorised, and a public network is expanded to hosts you approve first. Private ranges are treated as yours.
- Findings become tickets with a status (open, in progress, false positive, won't fix, resolved), the proof, a description, remediation, the CVE and its CVSS score, and a severity. A repeat scan reopens or resolves them.
- Devices: every host found is listed with its services, a best-guess type and whether it is online (checked every few minutes). You can rename it, add a description and tag it. Risky exposed services (telnet, SMB, RDP, VNC, open databases and others) become tickets.
- Dashboard, tickets, scan objects, a small editor for your own nuclei templates, and settings, in the same style as the other apps.
- Schedules per object (daily, weekly, monthly). Templates are kept current so scans use the latest CVEs.
- The database lives in /data, with a backup download and a delete-all danger zone.
- Optional light default-credential check against discovered services, off by default.
