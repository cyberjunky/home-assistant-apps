"""How the web UI connects to the app's own database."""

from src import config as config_module
from src.config import Config, integration_password


def test_defaults_are_the_local_socket(monkeypatch):
    for name in (
        "CHRONICLE_DB_HOST",
        "CHRONICLE_DB_PORT",
        "CHRONICLE_DB_USER",
        "CHRONICLE_DB_PASSWORD",
        "CHRONICLE_DB_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    config = Config.from_environment()
    assert (config.host, config.port, config.username, config.password, config.database) == (
        "/run/postgresql",
        5432,
        "chronicle_ui",
        "",
        "homeassistant",
    )


def test_log_level(monkeypatch):
    monkeypatch.setenv("CHRONICLE_LOG_LEVEL", "DEBUG")
    assert Config.from_environment().log_level == "debug"
    monkeypatch.setenv("CHRONICLE_LOG_LEVEL", "loud")
    assert Config.from_environment().log_level == "info"


def test_integration_connection_has_no_password():
    connection = Config().integration()
    assert connection["username"] == "chronicle"
    assert connection["database"] == "homeassistant"
    assert "password" not in connection


def test_integration_password(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "SECRETS_DIR", tmp_path)
    assert integration_password() is None
    (tmp_path / "chronicle.password").write_text("secret\n")
    assert integration_password() == "secret"
