"""Signing in, MFA, the token file and sign-out."""

import json

import pytest

from src.account import NotSignedIn, SignInError


async def test_sign_in_keeps_tokens_not_the_password(account):
    assert await account.login("someone@example.com", "secret") == "ok"
    assert account.signed_in
    stored = account.token_path.read_text()
    assert "secret" not in stored
    assert json.loads(stored)["di_refresh_token"]
    assert account.profile["created"] == "2023-03-14"


async def test_mfa_flow(account):
    assert await account.login("mfa@example.com", "secret") == "mfa"
    assert account.waiting_for_mfa and not account.signed_in
    with pytest.raises(SignInError):
        await account.submit_mfa("000000")
    # A wrong code doesn't end the sign-in: the right one still works
    await account.submit_mfa("123 456")
    assert account.signed_in and not account.waiting_for_mfa


async def test_wrong_password(account):
    with pytest.raises(SignInError, match="refused"):
        await account.login("someone@example.com", "wrong")
    assert not account.signed_in and not account.has_tokens


async def test_resume_from_saved_tokens(signed_in, tmp_path):
    from fake_garmin import FakeGarmin

    from src.account import GarminAccount

    again = GarminAccount(token_dir=signed_in.token_dir, factory=FakeGarmin)
    assert await again.resume()
    assert again.signed_in


async def test_sign_out_removes_token(signed_in):
    signed_in.sign_out()
    assert not signed_in.has_tokens
    with pytest.raises(NotSignedIn):
        await signed_in.call("get_devices")


async def test_empty_fields(account):
    with pytest.raises(SignInError):
        await account.login("", "")
