# Security policy

## Scope and intent

This app is a vulnerability scanner for **internal use against systems you own or are explicitly authorised to test**. It is a defensive tool: it finds weaknesses so you can fix them.

- **Ownership gate.** Scans run only against registered objects that are marked authorised. Public IPs and networks must be expanded to hosts you approve before scanning; host names, URLs and ASNs must be authorised explicitly. See `src/targets.py` and `src/engine.py`.
- **No arbitrary scanning.** The app never scans a target that was not registered and authorised through the dashboard. The API that changes anything is reachable only through Home Assistant's authenticated ingress.
- **Scanners.** nuclei and naabu run as fixed commands. Intrusive, denial-of-service and fuzzing templates are excluded by default. The optional default-credential check is off by default and uses a small built-in list, not a password file.
- **Custom templates** are validated by nuclei before they are saved and are stored inside one folder under `/data`.

## Reporting a vulnerability

Open an issue at https://github.com/cyberjunky/home-assistant-apps/issues, or for something sensitive contact the maintainer privately.
