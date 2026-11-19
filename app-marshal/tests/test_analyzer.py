"""Each check on the smallest code that should, and should not, set it off."""

import json

from src.analyzer import add_context, analyze, finish, parse_requirement
from src.analyzer.hosts import classify

MANIFEST = {
    "domain": "demo",
    "name": "Demo",
    "version": "1.0.0",
    "codeowners": ["@someone"],
    "issue_tracker": "https://github.com/someone/demo/issues",
    "iot_class": "cloud_polling",
    "config_flow": True,
    "requirements": [],
}


def scan(code: str = "", manifest: dict | None = None, extra: dict | None = None, **context) -> dict:
    files = {"manifest.json": json.dumps({**MANIFEST, **(manifest or {})}).encode(), "__init__.py": code.encode()}
    files.update(extra or {})
    report = analyze({path: (len(data), data) for path, data in files.items()})
    add_context(report, **context)
    return finish(report)


def rules(report: dict) -> set[str]:
    return {f["rule"] for f in report["findings"]}


def test_clean_integration_scores_100():
    report = scan("async def async_setup_entry(hass, entry):\n    return True\n")
    assert report["score"] == 100 and report["grade"] == "A" and not report["findings"]


def test_obfuscated_exec_is_critical():
    report = scan("import base64\nexec(base64.b64decode('cHJpbnQoMSk='))\n")
    assert "code.obfuscated_exec" in rules(report)
    assert report["counts"]["critical"] == 1 and report["grade"] == "F"


def test_eval_and_shell_and_install():
    code = "import os, subprocess\neval(x)\nos.system('ls')\nsubprocess.run(['pip', 'install', 'x'])\n"
    assert {"code.eval", "code.shell", "code.subprocess", "code.runtime_install"} <= rules(scan(code))


def test_subprocess_with_shell_is_a_shell_command():
    assert "code.shell" in rules(scan("import subprocess\nsubprocess.run(cmd, shell=True)\n"))


def test_a_method_named_eval_is_not_eval():
    assert "code.eval" not in rules(scan("model.eval()\n"))


def test_monkeypatching_home_assistant():
    code = "from homeassistant.helpers import entity\nentity.Entity.async_write_ha_state = patched\n"
    assert "code.monkeypatch" in rules(scan(code))


def test_auth_tokens_and_private_files():
    code = (
        "async def f(hass, user):\n"
        "    token = await hass.auth.async_create_refresh_token(user)\n"
        "    path = hass.config.path('.storage/auth')\n"
        "    other = hass.config.path('secrets.yaml')\n"
        "    entries = hass.config_entries.async_entries()\n"
    )
    assert {"data.auth_tokens", "data.private_storage", "data.secrets_file", "data.all_config_entries"} <= rules(
        scan(code)
    )


def test_own_config_entries_are_fine():
    assert "data.all_config_entries" not in rules(scan("entries = hass.config_entries.async_entries(DOMAIN)\n"))


def test_docstrings_do_nothing():
    assert not scan('"""Reads secrets.yaml and posts it to https://webhook.site/x."""\n')["findings"]


def test_logged_credentials():
    assert "data.log_secret" in rules(scan("_LOGGER.debug('login %s', self.password)\n"))
    assert "data.log_secret" in rules(scan("_LOGGER.debug(f'token {entry.data[\"access_token\"]}')\n"))
    assert "data.log_secret" not in rules(scan("_LOGGER.debug('expires %s', token_expires)\n"))


def test_hardcoded_secret_is_reported_without_its_value():
    secret = "9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"
    report = scan(f'CLIENT_SECRET = "{secret}"\nCONF_API_KEY = "api_key"\n')
    finding = next(f for f in report["findings"] if f["rule"] == "data.hardcoded_secret")
    assert finding["count"] == 1
    assert secret not in json.dumps(report)


def test_reading_all_states_and_talking_out():
    code = "URL = 'https://api.vendor.example.io/v1'\nasync def f(hass):\n    return hass.states.async_all()\n"
    assert {"data.all_states", "data.states_egress", "network.external_hosts"} <= rules(scan(code))
    assert "data.states_egress" not in rules(scan("async def f(hass):\n    return hass.states.async_all()\n"))


def test_unauthenticated_view_and_tls():
    code = "class V(HomeAssistantView):\n    requires_auth = False\nsession.get(url, verify_ssl=False)\n"
    report = scan(code)
    assert {"network.unauth_view", "network.tls_off"} <= rules(report)
    assert next(f for f in report["findings"] if f["rule"] == "network.tls_off")["severity"] == "medium"
    local = scan(code, {"iot_class": "local_polling"})
    assert next(f for f in local["findings"] if f["rule"] == "network.tls_off")["severity"] == "low"


def test_hosts():
    code = (
        "A = 'https://webhook.site/abc'\nB = 'http://203.0.113.9/x'\nC = 'http://192.168.1.2/api'\n"
        "D = 'https://github.com/someone/demo'\nE = f'http://{host}/api'\nF = 'http://api.vendor.com/v1'\n"
    )
    report = scan(code, {"iot_class": "local_polling"})
    kinds = {h["host"]: h["kind"] for h in report["hosts"]}
    assert kinds == {
        "webhook.site": "dropbox",
        "203.0.113.9": "local",  # a documentation range, not a real address
        "192.168.1.2": "local",
        "github.com": "reference",
        "api.vendor.com": "external",
    }
    assert {"network.dropbox_host", "network.plain_http", "network.local_claims_cloud"} <= rules(report)


def test_public_ip_and_chat_and_download():
    assert classify("https", "8.8.8.8/dns")["kind"] == "ip"
    assert classify("https", "discord.com/api/webhooks/1/abc")["kind"] == "chat"
    assert classify("https", "discord.com/invite/abc")["kind"] == "external"
    assert classify("https", "github.com/a/b/releases/download/v1/fw.bin")["download"]
    assert classify("https", "{host}/x") is None


def test_blocking_calls_only_count_in_the_event_loop():
    blocking = "import time, requests\nasync def f():\n    time.sleep(1)\n    requests.get(u)\n    open('x')\n"
    assert {"performance.blocking_sleep", "performance.blocking_http", "performance.blocking_io"} <= rules(
        scan(blocking)
    )
    fine = (
        "import time, requests\ndef f():\n    time.sleep(1)\n    requests.get(u)\n"
        "async def g(hass):\n    await hass.async_add_executor_job(requests.get, u)\n"
        "    await hass.async_add_executor_job(lambda: requests.get(u))\n"
    )
    assert not {r for r in rules(scan(fine)) if r.startswith("performance.")}


def test_callback_runs_in_the_event_loop():
    code = "import time\nfrom homeassistant.core import callback\n@callback\ndef f():\n    time.sleep(1)\n"
    assert "performance.blocking_sleep" in rules(scan(code))


def test_endless_loop():
    assert "performance.busy_loop" in rules(scan("async def f():\n    while True:\n        work()\n"))
    assert "performance.busy_loop" not in rules(scan("async def f():\n    while True:\n        await work()\n"))
    assert "performance.busy_loop" not in rules(
        scan("async def f():\n    while True:\n        if work():\n            break\n")
    )


def test_polling_intervals():
    code = "from datetime import timedelta\nSCAN_INTERVAL = timedelta(seconds=10)\nTIMEOUT = timedelta(seconds=2)\n"
    report = scan(code)
    assert "performance.fast_poll" in rules(report)
    assert next(f for f in report["findings"] if f["rule"] == "performance.fast_poll")["count"] == 1
    assert "performance.fast_poll" not in rules(scan(code, {"iot_class": "local_polling"}))
    assert "performance.tight_interval" in rules(
        scan("x = dict(update_interval=timedelta(seconds=1))\n", {"iot_class": "local_polling"})
    )


def test_listening_to_everything():
    assert "performance.all_events" in rules(scan("hass.bus.async_listen(EVENT_STATE_CHANGED, cb)\n"))
    assert "performance.all_events" not in rules(scan("hass.bus.async_listen('my_event', cb)\n"))


def test_binaries_and_bytecode():
    report = scan(extra={"lib/native.so": b"\x7fELF" + b"\0" * 12, "hidden.pyc": b"\0" * 16, "tool": b"\x7fELF\0\0"})
    finding = next(f for f in report["findings"] if f["rule"] == "code.binary")
    assert finding["count"] == 2 and "code.bytecode_only" in rules(report)


def test_syntax_error_is_reported_not_raised():
    assert "code.unparseable" in rules(scan("def broken(:\n"))


def test_manifest_rules():
    report = scan(
        manifest={"version": "", "codeowners": [], "issue_tracker": "", "iot_class": "", "config_flow": False}
    )
    assert {
        "upkeep.no_version",
        "upkeep.no_owner",
        "upkeep.no_issue_tracker",
        "upkeep.no_iot_class",
        "upkeep.yaml_only",
    } <= rules(report)
    missing = finish(analyze({"__init__.py": (0, b"")}))
    assert "upkeep.no_manifest" in rules(missing)


def test_requirements():
    assert parse_requirement("aiohttp==3.9.1") == {
        "name": "aiohttp",
        "spec": "==3.9.1",
        "version": "3.9.1",
        "pinned": True,
        "url": False,
    }
    assert not parse_requirement("aiohttp>=3.9")["pinned"]
    assert not parse_requirement("aiohttp==3.*")["pinned"]
    assert parse_requirement("pkg[extra]==1.0")["version"] == "1.0"
    assert parse_requirement("git+https://github.com/a/b.git#egg=thing")["url"]
    assert parse_requirement("thing @ https://example.com/thing.whl")["name"] == "thing"
    report = scan(
        manifest={"requirements": ["old==1.0", "loose>=2", "git+https://github.com/a/b.git"]},
        vulnerabilities={("old", "1.0"): [{"id": "CVE-2020-1", "summary": "bad", "severity": "high"}]},
    )
    assert {"code.vulnerable_dependency", "upkeep.unpinned_requirement", "code.url_requirement"} <= rules(report)


def test_catalog_and_runtime_context():
    report = scan(
        catalog={"full_name": "someone/demo", "last_updated": "2020-01-01T00:00:00Z", "stargazers_count": 5},
        removed={"repository": "someone/demo", "removal_type": "critical", "reason": "steals tokens"},
        runtime={"setup_seconds": 22.5, "errors": 40, "warnings": 1},
        now=1795000000,
    )
    assert {"upkeep.removed", "upkeep.abandoned", "performance.slow_setup", "performance.log_errors"} <= rules(report)
    assert report["repository"] == "someone/demo" and report["stars"] == 5 and report["grade"] == "F"
    assert "upkeep.delisted" in rules(scan(removed={"repository": "a/b", "removal_type": "remove"}))
    assert "upkeep.manual" in rules(scan(installed=True))
    custom = scan(installed=True, marketplace={"full_name": "a/b", "custom": True, "pending_upgrade": True})
    assert {"upkeep.custom_repository", "upkeep.update_available"} <= rules(custom)


def test_more_places_weigh_more_but_at_most_twice():
    one = scan("eval(a)\n")["scores"]["code"]
    many = scan("eval(a)\n" * 30)["scores"]["code"]
    assert one == 70 and many == 40


def test_one_bad_area_is_not_averaged_away():
    report = scan("eval(a)\n" * 30)
    assert report["scores"]["code"] == 40 and report["score"] == 55


def test_a_critical_finding_never_passes():
    report = scan("import base64\nexec(base64.b64decode(x))\n")
    assert report["scores"]["code"] == 40 and report["score"] == 45 and report["grade"] == "F"
