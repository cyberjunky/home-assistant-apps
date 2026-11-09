# Nuclei Scanner App for Home Assistant

> **Internal use.** A vulnerability scanner for systems you own or are authorised to test. It will not scan a target until you register it and confirm you are allowed to.

An internal vulnerability scanner with a ticket workflow, built on [nuclei](https://github.com/projectdiscovery/nuclei) and [naabu](https://github.com/projectdiscovery/naabu) from ProjectDiscovery. It finds open ports, known CVEs and exposed or misconfigured services, and turns each finding into a ticket you work through.

## What it does

- **Open ports** with naabu, then **CVEs and exposures** with nuclei's community templates, which carry essentially every public CVE that has a template. You don't curate lists, and a scan is thorough rather than quick: web templates run on web ports, and protocol templates (SSH, SMB, FTP, databases, TLS and more) on the other ports.
- **Devices.** Every host found is listed with its services, a best-guess type and whether it is online. Rename it, describe it and tag it (linux, switch, camera, doorbell, tv and so on).
- **Tickets.** Every finding is a ticket with a status, the proof, a description, remediation, the CVE and CVSS score, and a severity. A later scan reopens a ticket that comes back or resolves one that is gone.
- **Your own templates.** Paste nuclei templates in the app; they run alongside the community set.
- **Schedules.** Scan an object daily, weekly or monthly.

## The ownership gate

A scan only ever runs against objects you register, and only after you confirm you are authorised to scan them. A public IP or network is expanded to a list of hosts you see and approve before it can be scanned; a host name, URL or ASN must be authorised explicitly. Private ranges (RFC1918, loopback) are treated as yours. Do not point this at systems you do not own or have permission to test.

## Data

Everything lives in one SQLite database in the app's `/data` folder, included in Home Assistant backups. Settings has a **Database backup** download and a **Delete all data** danger zone.

## License

MIT, see [LICENSE](LICENSE).
