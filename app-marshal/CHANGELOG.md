# Changelog

## 0.1.0

- First version: static analysis of every custom integration under `custom_components`, nothing imported or run.
- **Five areas:** data & credentials, code safety, network, performance and upkeep, about 65 checks, each finding with file, line and code.
- **Score and grade** per integration, ranked worst first.
- **Context:** the Marketplace catalog (removed repositories, last change, installs), known vulnerabilities in pinned requirements (OSV), and setup time and log errors asked of Home Assistant.
- **Packages from PyPI:** each direct requirement is downloaded and read the same way, gets its own grade, and its serious findings and hosts count for the integration.
- **Check before you install:** a repository read from GitHub and scored.
- **Network page:** every host named in the code, and which integrations name it.
- **Save report:** the dashboard as one HTML file.
