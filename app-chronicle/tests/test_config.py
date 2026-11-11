"""The app options."""

import json

import pytest

from src.config import Config


def test_options(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"host": "db", "port": 6543, "username": "u", "password": "p", "database": "d"}))
    config = Config.from_options_file(path)
    assert (config.host, config.port, config.username, config.password, config.database) == ("db", 6543, "u", "p", "d")
    assert "password" not in config.public()


def test_missing_host_is_an_error(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"host": " "}))
    with pytest.raises(ValueError, match="host"):
        Config.from_options_file(path)


def test_bad_port_falls_back(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"host": "db", "port": 99999}))
    assert Config.from_options_file(path).port == 5432
