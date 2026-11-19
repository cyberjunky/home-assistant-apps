# Security policy

## Scope and intent

This app reads the code of Home Assistant custom integrations and reports what deserves a look. It is a
defensive tool: it reads, it never changes what it scans, and it never runs it.

- **Nothing is executed.** Python is parsed with `ast.parse`, manifests with `json.loads`. No file of
  an integration is imported, compiled to run, or started. See `src/analyzer/`.
- **Read-only.** Home Assistant's configuration folder is mounted read-only, and only
  `custom_components` is read. The app does not read `secrets.yaml` or `.storage`.
- **Repositories you check** are downloaded as an archive from `github.com` and read member by member.
  Nothing is unpacked to disk, so an archive has no path to write to. Size and file count are capped.
  The repository name is checked against a strict pattern before it becomes part of a URL.
- **Home Assistant** is asked three read-only questions over its WebSocket API. The app calls no
  service and changes no setting.
- **What leaves the machine** is listed in DOCS.md: package names and versions to OSV, and the name of
  a repository you check to GitHub. Nothing about your installation, entities or configuration.
- **Secrets.** A finding quotes the line of code it points at, except for a line that looks like a
  secret: that one is left out of the results and of a saved report.
- **Code lines are untrusted text.** They are shown escaped, never as HTML.
- **The dashboard and its API** answer only Home Assistant's signed-in ingress proxy.

## Limits

Static analysis finds patterns. It does not prove an integration safe, and a finding does not prove one
harmful. See "What Marshal cannot see" in DOCS.md.

## Reporting a vulnerability

Open an issue at https://github.com/cyberjunky/home-assistant-apps/issues, or for something sensitive
contact the maintainer privately.
