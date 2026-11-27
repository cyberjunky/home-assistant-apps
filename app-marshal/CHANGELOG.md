# Changelog

## 0.4.0

**After updating:** nothing to do. Everything is scanned again at start; integrations with the new findings score lower.

- **Actions any user can call:** Home Assistant lets every logged-in user, also one without admin rights, call actions and websocket commands. Marshal now follows each action an integration registers (plain, entity action or websocket command) into the code it calls, and reports one that writes, moves or deletes a file the caller names (high), reads one (medium), or runs a shell command (medium). Admin-only actions, actions that check for an administrator, paths checked with `is_allowed_path`, and files uploaded through Home Assistant are left out.
- **Path checks that let the whole configuration folder through:** that folder holds `configuration.yaml`, `secrets.yaml` and `.storage`, so a check that accepts anything inside it is a finding of its own.
- **Where credentials end up:** a password, token or key put in an entity's state, its attributes or an event (high: every user sees those, and history keeps them), or written to a file of the integration's own (medium).

## 0.3.0

**After updating:** nothing to do. Everything is scanned again at start, and all scores shift a little: the areas were reweighed to make room for code quality. The app now also connects to `api.github.com`, for security advisories (option **Known vulnerabilities**).

- **Code quality, a sixth area:** each integration is read by Ruff, the linter Home Assistant itself uses: names that are not defined, patterns that often hide a mistake, errors silently dropped, and functions too tangled to follow. Marshal adds how many functions have type hints. Style is not judged, nothing is run, and poor quality alone does not fail an integration. New option **Code quality** (on by default).
- **Security checks from Ruff** for what Marshal did not look at yet: queries pasted together from text, archives unpacked without checking, broken hashes and ciphers, old TLS versions, SSH without host key check, Telnet, FTP and SNMP, and web requests without a timeout.
- **Vulnerabilities all the way down:** what a requirement installs in turn is followed on PyPI and looked up in the vulnerability database too. A finding names the CVE, the requirement it comes in through, and the version that fixes it.
- **Severity** of a vulnerability is the database's: no longer everything high. The same vulnerability under two ids counts once.
- **Security advisories of the integration itself:** the repository's published advisories are fetched from GitHub; one that covers the installed version is a finding.
- A requirement whose version was **withdrawn from PyPI** is a finding, and a version **published in the last week** is a note.

## 0.2.4

- The choice between all, installed and checked integrations is no longer shown on the Check and How it works pages, where it did nothing.

## 0.2.3

- The top bar fits again: the choice between all, installed and checked integrations moved under it, and on a narrow screen the bar wraps instead of getting a scrollbar.

## 0.2.2

- Screenshots in the README.
- The dashboard opens on a given page, integration or theme from its address (`?tab=`, `?open=`, `?theme=`).

## 0.2.1

- **Packages page:** every package that was read from PyPI, worst first, with its findings and the integrations that install it. Search by package or integration, filter by grade, and open a package or one of its integrations from the row.

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
- **Check before you install:** a repository read from GitHub and scored.
- **Network page:** every host named in the code, and which integrations name it.
- **Save report:** the dashboard as one HTML file.
