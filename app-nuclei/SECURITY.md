# Security policy

## Scope and intent

This app is a vulnerability scanner for **internal use, on your own network**. It is a defensive tool: it
finds weaknesses so you can fix them.

- **What is scanned.** Only Home Assistant's device trackers that are home, with an address on your own
  network: private, loopback or link-local. A tracker with a public address, or none, is never scanned.
  See `src/trackers.py` (`scannable`) and `src/engine.py`.
- **No arbitrary scanning.** There is no way to enter an address, network or URL to scan. A device can
  be left out for good with **Never scan this device**.
- **Access.** The app reads the device trackers through the Supervisor with the token every app gets
  (`homeassistant_api`). The API that changes anything is reachable only through Home Assistant's
  authenticated ingress.
- **Scanners.** nuclei and naabu run as fixed commands. Intrusive, denial-of-service and fuzzing templates
  are excluded by default. The default-credential check (nuclei's default-login templates) is off by
  default: it makes real login attempts.
- **Custom templates** are validated by nuclei before they are saved and are stored inside one folder
  under `/data`.

## Reporting a vulnerability

Open an issue at https://github.com/cyberjunky/home-assistant-apps/issues, or for something sensitive
contact the maintainer privately.
