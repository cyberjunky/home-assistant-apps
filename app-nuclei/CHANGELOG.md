# Changelog

## 0.1.9

**After updating:** nothing to do.

- **The top bar fits a phone:** the app's name and buttons on top with the theme button last, the menu full width below. When the menu doesn't fit it scrolls sideways instead of losing a tab; on a laptop it gives way the same way and the clock goes first (below 860 px).

## 0.1.8

**After updating:** nothing to do.

- The top bar matches the other apps: the tabs in one group with the open one in solid blue, Settings after a separator, and the clock on the right. On a phone the tabs take the whole width.
- **Theme:** one button cycles **auto** (follows Home Assistant's light or dark mode), **light** and **dark**, as in the other apps. Nuclei still starts light; a choice made before stays.

## 0.1.7

**After updating:** nothing to do.

- Unused code removed; nothing changes in how the app works.

## 0.1.6

**After updating:** nothing to do.

- The top bar is as high as Home Assistant's own header next to it (the sidebar's "Home Assistant" bar), so the two line up. It follows Home Assistant's header height, 56 px by default.

## 0.1.5

**After updating:** nothing to do.

- The dashboard starts in the light theme. The theme button still switches to dark, and the
  browser remembers that choice.

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
