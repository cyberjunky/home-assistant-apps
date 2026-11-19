"""Every check Marshal knows: its category, how much it weighs, and what it means in plain words.

A finding is a lead for a person to look at, not a verdict. Static analysis sees what code could do, not
what it does: a notification integration posts to a chat service on purpose, and an integration for a
device with a self-signed certificate turns certificate checks off because it has to.
"""

from dataclasses import dataclass

CATEGORIES = {
    "data": "Data & credentials",
    "code": "Code safety",
    "network": "Network",
    "performance": "Performance",
    "upkeep": "Upkeep",
}
SEVERITIES = ("critical", "high", "medium", "low", "info")


@dataclass(frozen=True)
class Rule:
    category: str
    severity: str
    title: str
    why: str


def _r(category: str, severity: str, title: str, why: str) -> Rule:
    return Rule(category, severity, title, why)


RULES: dict[str, Rule] = {
    # -- data & credentials -------------------------------------------------------------------------
    "data.auth_tokens": _r(
        "data",
        "high",
        "Works with Home Assistant's logins and tokens",
        "Calls the part of Home Assistant that makes access tokens or lists users. A token made here gives "
        "full access to your Home Assistant. Few integrations have a reason to do this.",
    ),
    "data.private_storage": _r(
        "data",
        "high",
        "Names Home Assistant's private storage files",
        "Refers to files under .storage that hold logins, tokens, or every integration's saved credentials.",
    ),
    "data.secrets_file": _r(
        "data",
        "high",
        "Names secrets.yaml",
        "Refers to the file where your passwords and keys are kept. Integrations get their settings from "
        "Home Assistant and do not need to read it.",
    ),
    "data.host_secrets": _r(
        "data",
        "high",
        "Names SSH keys or system password files",
        "Refers to private keys or the system's account files.",
    ),
    "data.all_config_entries": _r(
        "data",
        "medium",
        "Reads every integration's configuration",
        "Asks Home Assistant for all config entries, not only its own. Those hold the credentials of every "
        "integration you have set up.",
    ),
    "data.supervisor_token": _r(
        "data",
        "medium",
        "Uses the Supervisor token",
        "Reads the token that gives access to the Supervisor: apps, backups and the host.",
    ),
    "data.log_secret": _r(
        "data",
        "medium",
        "May write credentials to the log",
        "A log line includes a value whose name says password, token, secret or key. Logs are shared in bug "
        "reports and readable by other integrations and apps.",
    ),
    "data.hardcoded_secret": _r(
        "data",
        "medium",
        "A secret is written in the code",
        "Something that looks like a key, token or private key is part of the source. Everyone who installs "
        "it shares that secret.",
    ),
    "data.diagnostics_unredacted": _r(
        "data",
        "low",
        "Diagnostics are not redacted",
        "Has a diagnostics download that does not use Home Assistant's redaction helper, so credentials and "
        "location can end up in a file people attach to public issues.",
    ),
    "data.telemetry": _r(
        "data",
        "medium",
        "Includes telemetry or error tracking",
        "Imports an analytics or error-tracking service, or names one of their hosts. Those send data about "
        "your installation to a third party.",
    ),
    "data.states_egress": _r(
        "data",
        "medium",
        "Reads all your entities and talks to the internet",
        "Reads the state of everything in Home Assistant (or listens to every change) and also contacts "
        "hosts on the internet. That is how a cloud bridge works, and also how data leaves a house.",
    ),
    "data.recorder_db": _r(
        "data",
        "medium",
        "Names the history database",
        "Refers to Home Assistant's history database file directly.",
    ),
    "data.all_states": _r(
        "data",
        "low",
        "Reads the state of every entity",
        "Asks for all states, not only its own entities: presence, locks, cameras, everything.",
    ),
    "data.storage_dir": _r(
        "data",
        "low",
        "Reads or writes under .storage by path",
        "Uses the .storage folder by its path rather than Home Assistant's storage helper.",
    ),
    "data.environ": _r(
        "data",
        "info",
        "Reads environment variables",
        "Reads values from the environment Home Assistant runs in.",
    ),
    # -- code safety --------------------------------------------------------------------------------
    "code.obfuscated_exec": _r(
        "code",
        "critical",
        "Runs code that is hidden in encoded data",
        "Decodes or unpacks data and runs the result as code. Honest code has no reason to hide what it runs.",
    ),
    "code.eval": _r(
        "code",
        "high",
        "Runs text as code (eval or exec)",
        "Whatever reaches this call is run with all of Home Assistant's rights.",
    ),
    "code.shell": _r(
        "code",
        "high",
        "Runs shell commands",
        "Hands a command line to the system shell. Anything that ends up in that line is run.",
    ),
    "code.runtime_install": _r(
        "code",
        "high",
        "Installs software while running",
        "Installs Python packages itself rather than listing them in its manifest, so what gets installed "
        "is not what you were shown.",
    ),
    "code.monkeypatch": _r(
        "code",
        "high",
        "Changes Home Assistant's own code while running",
        "Replaces a function or value inside Home Assistant itself. That changes behaviour for everything, "
        "and breaks on updates.",
    ),
    "code.binary": _r(
        "code",
        "high",
        "Ships a compiled program",
        "The package contains a native binary. Nobody can read what it does.",
    ),
    "code.bytecode_only": _r(
        "code",
        "high",
        "Ships compiled Python without its source",
        "A .pyc file without the .py next to it: code that runs but cannot be read.",
    ),
    "code.url_requirement": _r(
        "code",
        "high",
        "Installs a requirement from a web address",
        "A requirement comes from a URL or git repository rather than PyPI, so it has no fixed, published version.",
    ),
    "code.vulnerable_dependency": _r(
        "code",
        "high",
        "Depends on a package with a known vulnerability",
        "The exact version in the manifest is listed in the OSV vulnerability database.",
    ),
    "code.dependency_critical": _r(
        "code",
        "high",
        "A package it installs has a critical finding",
        "A requirement from PyPI runs hidden code or has another critical finding. The package runs inside "
        "Home Assistant like the integration itself. Open the package to see where.",
    ),
    "code.dependency_missing": _r(
        "code",
        "medium",
        "A requirement is not on PyPI",
        "PyPI does not have this package or version. It was removed, or the name is wrong; either way "
        "anyone can publish under a free name.",
    ),
    "code.dependency_findings": _r(
        "code",
        "medium",
        "A package it installs has serious findings",
        "A requirement from PyPI has findings of high severity. Open the package to see what and where.",
    ),
    "code.subprocess": _r(
        "code",
        "medium",
        "Starts other programs",
        "Runs a program on the system Home Assistant runs on.",
    ),
    "code.deserialize": _r(
        "code",
        "medium",
        "Loads pickled data",
        "Unpickling runs code that the data asks for. Safe only for data the integration wrote itself.",
    ),
    "code.yaml_load": _r(
        "code",
        "medium",
        "Loads YAML without the safe loader",
        "The unsafe YAML loader can build any Python object, which can run code.",
    ),
    "code.downloads": _r(
        "code",
        "medium",
        "Names a download of code or an archive",
        "A web address in the code points at a script, archive or program.",
    ),
    "code.dynamic_import": _r(
        "code",
        "low",
        "Imports modules chosen while running",
        "Which module is loaded is decided by a value, not written in the code.",
    ),
    "code.native": _r(
        "code",
        "low",
        "Calls native libraries",
        "Uses ctypes or cffi to call code outside Python.",
    ),
    "code.blob": _r(
        "code",
        "low",
        "Contains a large encoded blob",
        "A long block of encoded data. Usually an image; worth a glance.",
    ),
    "code.shell_script": _r(
        "code",
        "low",
        "Ships shell scripts",
        "The package contains shell scripts.",
    ),
    "code.unparseable": _r(
        "code",
        "low",
        "A Python file could not be read",
        "The file has a syntax error or is too large, so it was not analyzed.",
    ),
    "code.dependency_unread": _r(
        "code",
        "info",
        "A package it installs was not read",
        "The requirement could not be downloaded or read, so what it does is not known.",
    ),
    "code.compiled": _r(
        "code",
        "info",
        "Contains compiled code",
        "Part of this package is compiled. Only its Python was read.",
    ),
    "code.frontend": _r(
        "code",
        "info",
        "Ships front-end JavaScript",
        "JavaScript that runs in your browser inside Home Assistant. Marshal counts it but does not analyze it.",
    ),
    # -- network ------------------------------------------------------------------------------------
    "network.unauth_view": _r(
        "network",
        "high",
        "Adds a web endpoint that needs no login",
        "Registers an address on Home Assistant that answers without a login. Anyone who can reach your "
        "Home Assistant can call it.",
    ),
    "network.dropbox_host": _r(
        "network",
        "high",
        "Names a host used to collect or tunnel data",
        "A paste site, request catcher, tunnel or file drop is written in the code.",
    ),
    "network.chat_webhook": _r(
        "network",
        "medium",
        "Names a chat service's webhook or bot address",
        "Normal for a notification integration; for anything else, a simple way to send data out.",
    ),
    "network.local_claims_cloud": _r(
        "network",
        "medium",
        "Says it is local, but names internet hosts",
        "The manifest declares a local integration, and the code contains addresses on the internet.",
    ),
    "network.ip_literal": _r(
        "network",
        "medium",
        "Names a public IP address",
        "A fixed address on the internet rather than a name: nothing says who it belongs to.",
    ),
    "network.tls_off": _r(
        "network",
        "medium",
        "Turns certificate checks off",
        "Connections are made without checking who answers. Common for devices with a self-signed "
        "certificate on your own network; on the internet it lets anyone in between read along.",
    ),
    "network.plain_http": _r(
        "network",
        "low",
        "Uses unencrypted connections to the internet",
        "An address on the internet is contacted without encryption.",
    ),
    "network.many_hosts": _r(
        "network",
        "low",
        "Names many different internet hosts",
        "More than six separate services appear in the code.",
    ),
    "network.listener": _r(
        "network",
        "low",
        "Opens its own network port",
        "Starts a server of its own, next to Home Assistant's.",
    ),
    "network.cors": _r(
        "network",
        "low",
        "Allows its endpoint to be called from other websites",
        "A web endpoint is opened to requests from any website.",
    ),
    "network.raw_socket": _r(
        "network",
        "info",
        "Uses network sockets directly",
        "Talks on the network below HTTP: normal for device protocols.",
    ),
    "network.external_hosts": _r(
        "network",
        "info",
        "Talks to hosts on the internet",
        "The internet addresses written in the code. See the Network page for the list.",
    ),
    # -- performance --------------------------------------------------------------------------------
    "performance.busy_loop": _r(
        "performance",
        "high",
        "An endless loop that never yields",
        "A while-true loop in async code without an await: once entered, Home Assistant stops responding.",
    ),
    "performance.blocking_sleep": _r(
        "performance",
        "high",
        "Sleeps inside Home Assistant's event loop",
        "time.sleep in async code freezes all of Home Assistant for that long.",
    ),
    "performance.blocking_http": _r(
        "performance",
        "high",
        "Blocking web requests inside the event loop",
        "A synchronous HTTP call in async code: everything waits until the other side answers.",
    ),
    "performance.blocking_subprocess": _r(
        "performance",
        "medium",
        "Waits for a program inside the event loop",
        "Runs a program and waits for it in async code.",
    ),
    "performance.blocking_socket": _r(
        "performance",
        "medium",
        "Blocking network calls inside the event loop",
        "A name lookup or connection that waits, in async code.",
    ),
    "performance.all_events": _r(
        "performance",
        "medium",
        "Runs on every state change",
        "Listens to all events or all state changes. Its code runs thousands of times an hour on a busy system.",
    ),
    "performance.fast_poll": _r(
        "performance",
        "medium",
        "Polls a cloud service very often",
        "Asks a service on the internet for data more than twice a minute.",
    ),
    "performance.slow_setup": _r(
        "performance",
        "medium",
        "Slow to start",
        "Took long to set up when Home Assistant last started.",
    ),
    "performance.log_errors": _r(
        "performance",
        "medium",
        "Logs many errors",
        "Home Assistant's log has repeated errors from this integration since the last start.",
    ),
    "performance.tight_interval": _r(
        "performance",
        "low",
        "Polls every few seconds",
        "Updates more often than once every five seconds.",
    ),
    "performance.blocking_io": _r(
        "performance",
        "low",
        "Reads or writes files inside the event loop",
        "File access in async code. Short on an SSD, long on an SD card.",
    ),
    "performance.own_session": _r(
        "performance",
        "low",
        "Makes its own HTTP sessions",
        "Creates its own client session rather than using Home Assistant's shared one: more connections "
        "and memory, and Home Assistant's network settings are skipped.",
    ),
    "performance.log_warnings": _r(
        "performance",
        "low",
        "Logs errors or warnings",
        "Home Assistant's log has errors or warnings from this integration since the last start.",
    ),
    "performance.large": _r(
        "performance",
        "info",
        "A large package",
        "More than 25,000 lines of Python or 20 MB on disk.",
    ),
    # -- upkeep -------------------------------------------------------------------------------------
    "upkeep.removed": _r(
        "upkeep",
        "critical",
        "Removed from the Marketplace for security reasons",
        "The catalog lists this repository as removed and marks it critical.",
    ),
    "upkeep.delisted": _r(
        "upkeep",
        "medium",
        "Removed from the Marketplace catalog",
        "The catalog lists this repository as removed. It no longer gets updates through the Marketplace.",
    ),
    "upkeep.no_manifest": _r(
        "upkeep",
        "high",
        "No readable manifest",
        "manifest.json is missing or not valid, so nothing about this integration is declared.",
    ),
    "upkeep.abandoned": _r(
        "upkeep",
        "medium",
        "No changes for more than two years",
        "The repository has not been touched for a long time. Problems found now are unlikely to be fixed.",
    ),
    "upkeep.stale": _r(
        "upkeep",
        "low",
        "No changes for more than a year",
        "The repository has been quiet for over a year.",
    ),
    "upkeep.custom_repository": _r(
        "upkeep",
        "low",
        "Not in the Marketplace catalog",
        "Added as a custom repository. It did not go through the catalog's checks.",
    ),
    "upkeep.update_available": _r(
        "upkeep",
        "low",
        "An update is available",
        "A newer version than the one installed has been released.",
    ),
    "upkeep.unpinned_requirement": _r(
        "upkeep",
        "low",
        "A requirement without a fixed version",
        "The manifest does not pin an exact version, so what gets installed changes over time.",
    ),
    "upkeep.no_owner": _r(
        "upkeep",
        "low",
        "Nobody is named as owner",
        "The manifest lists no code owners.",
    ),
    "upkeep.no_issue_tracker": _r(
        "upkeep",
        "low",
        "No place to report problems",
        "The manifest has no issue tracker.",
    ),
    "upkeep.no_version": _r(
        "upkeep",
        "low",
        "No version",
        "The manifest has no version, so updates cannot be told apart.",
    ),
    "upkeep.no_iot_class": _r(
        "upkeep",
        "low",
        "Does not say whether it is local or cloud",
        "The manifest has no iot_class.",
    ),
    "upkeep.yaml_only": _r(
        "upkeep",
        "info",
        "Set up in YAML only",
        "No config flow: credentials are written in configuration.yaml rather than entered in the UI.",
    ),
    "upkeep.manual": _r(
        "upkeep",
        "info",
        "Installed by hand",
        "The Marketplace does not know this integration, so it gets no update notices.",
    ),
}

WEIGHT = {"critical": 60, "high": 30, "medium": 12, "low": 4, "info": 0}
CATEGORY_WEIGHT = {"data": 0.30, "code": 0.25, "network": 0.20, "performance": 0.15, "upkeep": 0.10}
