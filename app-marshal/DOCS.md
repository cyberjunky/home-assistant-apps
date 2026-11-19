# Home Assistant App: Marshal

Reads the code of your custom integrations and ranks them by how much they deserve a second look.

## Setup

1. Install and start the app, then open it from the sidebar.
2. The first scan starts by itself. Every folder under `custom_components` is read; it takes seconds.
3. Open an integration to see its findings, each with the file, the line and the code.

## Why

A custom integration is Python that runs inside Home Assistant. It can read every credential you have
entered, every entity state, and every file Home Assistant can read, and it can talk to any host. The
Marketplace (Home Assistant 2026.11 and later; HACS before that) lists integrations and installs them.
It does not test, audit or vouch for them.

## How it reads code

- Python files are parsed to a syntax tree and searched for patterns.
- `manifest.json` is read as JSON.
- Every other file is measured, and its first bytes are looked at to recognise a compiled program.

**Nothing from an integration is imported or run.** A repository you check before installing is read
straight from its archive in memory; nothing is unpacked.

## Packages from PyPI

Most of what a cloud integration does on the network is in the library it installs, not in the
integration. So each **direct requirement** in a manifest is resolved on PyPI (the pinned version, or
the newest that fits), downloaded, checked against PyPI's checksum, and read with the same analyzer.
Nothing is installed.

- A pure Python wheel is read as it is: that is exactly what gets installed. Otherwise the source
  archive is read. Of a compiled package only the Python part is read, and the package says so.
- A package gets its own score and grade; open it from the integration's **Packages it installs**, or
  from the **Packages** page, which lists every package that was read, worst first, with the
  integrations that install it.
- Only a package's **high and critical** findings count against the integration: a critical one as
  high, high ones as medium. Popular libraries have many small findings that say nothing about the
  integration that uses them.
- The internet hosts a package names are added to the integration's, marked with the package.
- A requirement PyPI does not have is a finding of its own.
- A package is judged as a library: no manifest checks, and no blocking-call checks.

Not read: packages that Home Assistant itself ships (every installation has those), packages over
40 MB, and what a package in turn requires.

## The five areas

| Area | Weight | What is looked for |
|---|---|---|
| **Data & credentials** | 30% | Home Assistant's logins and tokens; `secrets.yaml`, `.storage` and SSH keys by name; all config entries; the Supervisor token; credentials in log lines; secrets in code; unredacted diagnostics; telemetry; reading all entities while naming internet hosts |
| **Code safety** | 25% | `eval`/`exec`; running decoded data; shell commands and other programs; pip at run time; assignments into `homeassistant.*`; pickle and unsafe YAML; compiled binaries; `.pyc` without source; requirements from a URL; requirements with a known vulnerability |
| **Network** | 20% | Views with `requires_auth = False`; paste sites, request catchers and tunnels; chat webhooks; public IP addresses; certificate checks off; plain http; own listening ports; `iot_class: local_*` with internet hosts |
| **Performance** | 15% | `time.sleep`, `requests`, `urlopen`, subprocesses, sockets and file access called directly from `async def` or `@callback` code; endless loops without an await; listeners on all events; cloud polling under 30 seconds; own HTTP sessions; setup time and log errors |
| **Upkeep** | 10% | Removed from the catalog; no changes for one or two years; custom repository; update waiting; unpinned requirements; missing owner, issue tracker, version or `iot_class` |

## Score and grade

Each area starts at 100. A finding takes off points by its severity: critical 60, high 30, medium 12,
low 4; notes (info) take nothing. The same finding in more places weighs more, up to twice. The overall
score is the weighted average of the areas, and never more than 15 above the weakest area, so one bad
area is not averaged away. An integration with a critical finding scores 45 at most.

**A** 90 and up, **B** 80, **C** 65, **D** 50, **F** below.

One adjustment: certificate checks turned off counts as low, not medium, for an integration that
declares itself local. A device on your network with a self-signed certificate is the usual reason.

## What a finding means

A finding says the code **could** do something, and shows you where. It does not say the integration
does harm. Some examples of findings that are expected:

- A notification integration names a chat service's webhook address.
- A camera integration turns certificate checks off.
- An integration that runs your own scripts uses `exec`.
- An integration that manages Home Assistant users calls the login API.

Read the line the finding points at, and decide.

## What Marshal cannot see

- **Requirements of requirements.** Direct requirements are read; what they install in turn is not.
- **Compiled code.** In an integration it is a finding; in a package it is noted and not read.
- **Front-end JavaScript.** Counted, not analyzed.
- **Code that hides well.** Text built up at run time, or behaviour that depends on data from the
  internet, does not show in a syntax tree.
- **A release that differs from its repository.** A check before install reads the tagged source. A
  Marketplace install may use a release's zip file instead. After installing, the installed files are
  what gets scanned.

## Check before you install

On the **Check** page, give a repository as `owner/name`. Marshal downloads the latest release's source
from GitHub (or the default branch when there is no release), reads it, and scores it. Public
repositories up to 100 MB.

## Save report

**Save report** downloads the whole dashboard as one HTML file that works offline. It contains the
lines of code the findings quote; a line that looks like a secret is left out.

## What the app connects to

| Host | When | What is sent |
|---|---|---|
| `data-v2.hacs.xyz` | Once a day (option *Marketplace catalog*) | Nothing; the catalog is downloaded |
| `analytics.home-assistant.io` | Once a day (same option) | Nothing; install counts are downloaded |
| `api.osv.dev` | Each scan (option *Known vulnerabilities*) | The package names and versions pinned in manifests |
| `pypi.org`, `files.pythonhosted.org` | Each scan, for packages not read before (option *Read the packages*) | The package names and versions from manifests |
| `raw.githubusercontent.com` | Once a week (same option) | Nothing; the list of packages Home Assistant ships is downloaded |
| `github.com` | When you use **Check** | The repository you asked for |
| Home Assistant Core | Each scan (option *Ask Home Assistant*) | Three read-only questions: setup times, the log, the Marketplace's installs |

Turn all four options off and the app never leaves your machine, except for **Check**.

## Options

| Option | Default | Meaning |
|---|---|---|
| `rescan_hours` | 24 | Scan everything again after this many hours |
| `catalog_lookups` | on | Fetch the Marketplace catalog and install counts |
| `vulnerability_lookups` | on | Look pinned requirements up in OSV |
| `dependency_scans` | on | Download each direct requirement from PyPI and read it |
| `runtime_checks` | on | Ask Home Assistant for setup times, log entries and Marketplace installs |
| `log_level` | info | The app's own log |

## Data

Results are kept in the app's data folder (`results.json`), with 90 days of the average score. What
was read of each package version is kept too (`packages/`), so a package is downloaded once. Nothing
of your configuration is copied except the code lines findings quote.

## Development

    python3 -m tools.mock --custom-components ~/dev/my-integration/custom_components   # http://localhost:8099
    python3 -m tools.survey --local ~/dev --top 100 --out /tmp/survey                  # one HTML file
    python3 -m pytest

Needs Python 3.14, like Home Assistant: the analyzer parses with the Python it runs on.
