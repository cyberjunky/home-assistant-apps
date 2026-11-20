import pytest

from src.config import Config
from src.webui import public_config

SHARED = {"username": "shared-user", "password": "shared-secret", "ws_token": "shared-token"}
BOT = {"name": "One", "url": "http://127.0.0.1:8080"}


def config(bot):
    return Config.from_options({"credentials": SHARED, "bots": [bot]})


def test_inherit_login_and_token():
    bot = config(BOT).bots[0]
    assert (bot.username, bot.password, bot.ws_token) == tuple(SHARED.values())


def test_override_is_a_complete_pair_and_does_not_inherit_token():
    bot = config({**BOT, "username": "other", "password": "private"}).bots[0]
    assert (bot.username, bot.password, bot.ws_token) == ("other", "private", "")


@pytest.mark.parametrize("field", ["username", "password"])
def test_partial_override_is_rejected(field):
    with pytest.raises(ValueError):
        config({**BOT, field: "override"})


def test_explicit_empty_token_uses_login():
    assert config({**BOT, "ws_token": ""}).bots[0].ws_token == ""


def test_no_secrets_in_public_status():
    status = str(public_config(config(BOT)))
    assert SHARED["password"] not in status
    assert SHARED["ws_token"] not in status


def test_existing_explicit_configuration_still_works():
    bot = Config.from_options({"bots": [{**BOT, "username": "old", "password": "secret"}]}).bots[0]
    assert bot.username == "old"
