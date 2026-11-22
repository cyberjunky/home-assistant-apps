"""The options, the development overrides and finding the Chronicle app."""

import json

from src.config import Config, pick_chronicle


def test_defaults_and_overrides(tmp_path, monkeypatch):
    for name in ("VIGIL_DB_HOST", "VIGIL_DB_PORT", "VIGIL_DB_NAME", "VIGIL_DB_USER", "VIGIL_DB_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("VIGIL_LOG_LEVEL", raising=False)
    config = Config.load(tmp_path / "missing.json")
    assert (config.db_host, config.db_port, config.db_name, config.db_user) == ("", 5432, "vigil", "vigil")
    options = {
        "database_host": "db.local",
        "database_password": "secret",
        "ingest_token": "token",
        "scrape_targets": [
            {"name": "nas", "url": "http://nas:9100/metrics"},
            {"name": "nas", "url": "http://other/metrics"},
            {"name": "router", "url": "http://router/metrics", "interval": 5},
        ],
        "logs_retention_days": 1000,
        "log_level": "debug",
    }
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options))
    monkeypatch.setenv("VIGIL_DB_PORT", "55433")
    config = Config.load(path)
    assert config.db_host == "db.local" and config.db_port == 55433
    assert [t.name for t in config.scrape_targets] == ["nas", "router"]
    assert config.scrape_targets[0].interval == 60 and config.scrape_targets[1].interval == 10
    assert config.logs_retention_days == 365
    assert config.log_level == "debug"


def test_secrets_stay_out_of_repr_and_public():
    config = Config(db_password="pw-one", ingest_token="pw-two", fritzbox_password="pw-three")
    for text in (repr(config), json.dumps(config.public())):
        assert "pw-" not in text


def test_notify_target():
    assert Config(notify_service="notify.mobile_app_phone").notify_target == "mobile_app_phone"
    assert Config(notify_service="mobile_app_phone").notify_target == "mobile_app_phone"
    assert Config().notify_target is None


def test_pick_chronicle():
    addons = [
        {"slug": "core_mosquitto", "state": "started"},
        {"slug": "abc123_app-chronicle", "state": "stopped"},
        {"slug": "def456_app-chronicle", "state": "started"},
    ]
    assert pick_chronicle(addons) == "def456_app-chronicle"
    assert pick_chronicle([{"slug": "x"}]) is None


def test_chronicle_is_found_next_to_vigil_by_host_name():
    from src.config import sibling_chronicle_host

    assert sibling_chronicle_host("a0d7b954-app-vigil") == "a0d7b954-app-chronicle"
    assert sibling_chronicle_host("local-app-vigil") == "local-app-chronicle"
    assert sibling_chronicle_host("app-vigil") is None
    assert sibling_chronicle_host("some-laptop") is None


def test_fritzbox_options_in_their_own_section_and_the_old_flat_names():
    from src.config import Config

    nested = Config.from_options({"fritzbox": {"host": "192.168.178.1", "user": "vigil", "password": "pw"}}, {})
    assert (nested.fritzbox_host, nested.fritzbox_user, nested.fritzbox_password) == ("192.168.178.1", "vigil", "pw")
    flat = Config.from_options({"fritzbox_host": "fritz.box", "fritzbox_user": "u", "fritzbox_password": "p"}, {})
    assert (flat.fritzbox_host, flat.fritzbox_user, flat.fritzbox_password) == ("fritz.box", "u", "p")
    assert Config.from_options({"fritzbox": {"host": "", "user": "", "password": ""}}, {}).fritzbox_host == ""
