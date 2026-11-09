# Changelog

## 0.1.4

**After updating:** nothing to do.

- Templates tab now shows how many community templates are installed and when they were last
  updated.
- The "Last scan" dashboard figure shows the actual time ago (e.g. "7 min ago"), not just the date,
  with the exact time on hover.
- A ticket or scan on a named device now shows that name next to its address, instead of a bare
  IP, in the Tickets tab, the ticket detail and the dashboard.
- Fixed: the dashboard's "Newest open tickets" rows did nothing when clicked; they now open the
  ticket, like the rows on the Tickets tab always have.

## 0.1.3

**After updating:** nothing to do.

- Fixed: dates showed as an ambiguous all-numeric "09/11/2026", which reads as 9 November or
  September 11 depending on the browser's locale. Dates now spell the month out (e.g. "09 Nov 2026").
- Every scan now says what it actually did: which passes ran, on how many targets, and whether
  the default-credential check was on, both in the app's log and as a tooltip on its status in
  Recent scans.

## 0.1.2

**After updating:** nothing to do.

- A Rescan button in the device detail window runs a new scan of the scan object that device belongs to.

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
