# Changelog

## 0.2.0

- **Packages from PyPI:** each direct requirement of an integration is downloaded from PyPI and read the same way, without installing it. A package gets its own grade, opened from the integration's **Packages it installs**.
- A package's high and critical findings count for the integration, and the hosts it names are added to the integration's, marked with the package.
- A requirement that PyPI does not have is a finding of its own.
- **Unpinned requirements** are now looked up in the vulnerability database too, at the version that would be installed.
- Packages Home Assistant itself ships are skipped. New option **Read the packages** (on by default); the app now also connects to `pypi.org`, `files.pythonhosted.org` and `raw.githubusercontent.com`.

## 0.1.0

- First version: static analysis of every custom integration under `custom_components`, nothing imported or run.
- **Five areas:** data & credentials, code safety, network, performance and upkeep, about 65 checks, each finding with file, line and code.
- **Score and grade** per integration, ranked worst first.
- **Context:** the Marketplace catalog (removed repositories, last change, installs), known vulnerabilities in pinned requirements (OSV), and setup time and log errors asked of Home Assistant.
<<<<<<< HEAD
=======
>>>>>>> origin/main
- **Check before you install:** a repository read from GitHub and scored.
- **Network page:** every host named in the code, and which integrations name it.
- **Save report:** the dashboard as one HTML file.
