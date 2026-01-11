# delete-me-discord api behavior tests
import sys
from pathlib import Path

import pytest
import requests

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.api import DiscordAPI
from delete_me_discord.utils import AuthenticationError, ResourceUnavailable, UnexpectedStatus, ReachedMaxRetries


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_request_retries_on_rate_limit_then_success(monkeypatch):
    api = DiscordAPI(token="token", max_retries=2, retry_time_buffer=(0, 0))
    responses = [
        FakeResponse(429, {"retry_after": 0}),
        FakeResponse(200, [{"ok": True}]),
    ]

    def fake_request(*args, **kwargs):
        return responses.pop(0)

    sleep_calls = []
    monkeypatch.setattr(api.session, "request", fake_request)
    monkeypatch.setattr("delete_me_discord.api.time.sleep", lambda s: sleep_calls.append(s))

    data = api._request("http://example.com", description="test", method="get")
    assert data == [{"ok": True}]
    assert len(sleep_calls) == 1


def test_request_raises_on_unauthorized_and_unavailable(monkeypatch):
    api = DiscordAPI(token="token", max_retries=0, retry_time_buffer=(0, 0))

    monkeypatch.setattr(api.session, "request", lambda **_: FakeResponse(401, {}))
    with pytest.raises(AuthenticationError):
        api._request("http://example.com", description="test", method="get")

    monkeypatch.setattr(api.session, "request", lambda **_: FakeResponse(403, {}))
    with pytest.raises(ResourceUnavailable):
        api._request("http://example.com", description="test", method="get")


def test_request_unexpected_status_for_delete(monkeypatch):
    api = DiscordAPI(token="token", max_retries=0, retry_time_buffer=(0, 0))
    monkeypatch.setattr(api.session, "request", lambda **_: FakeResponse(200, {}))
    with pytest.raises(UnexpectedStatus):
        api._request("http://example.com", description="delete", method="delete")


def test_delete_own_reaction_requires_emoji_identifier(monkeypatch):
    api = DiscordAPI(token="token", max_retries=0, retry_time_buffer=(0, 0))
    monkeypatch.setattr(api, "_request", lambda *_, **__: (_ for _ in ()).throw(AssertionError("should not call")))
    assert api.delete_own_reaction("c1", "m1", emoji={}) is False


def test_delete_own_reaction_encodes_identifier(monkeypatch):
    api = DiscordAPI(token="token", max_retries=0, retry_time_buffer=(0, 0))
    captured = {}

    def fake_request(url, description, method="get", params=None):
        captured["url"] = url
        captured["method"] = method
        return []

    monkeypatch.setattr(api, "_request", fake_request)
    assert api.delete_own_reaction("c1", "m1", emoji={"name": "smile", "id": "123"}) is True
    assert captured["method"] == "delete"
    assert "/reactions/smile%3A123/@me" in captured["url"]


def test_request_reaches_max_retries_on_network_error(monkeypatch):
    api = DiscordAPI(token="token", max_retries=0, retry_time_buffer=(0, 0))

    def boom(*args, **kwargs):
        raise requests.RequestException("offline")

    monkeypatch.setattr(api.session, "request", boom)
    monkeypatch.setattr("delete_me_discord.api.time.sleep", lambda *_: None)

    with pytest.raises(ReachedMaxRetries):
        api._request("http://example.com", description="test", method="get")


def test_request_reaches_max_retries_on_500(monkeypatch):
    api = DiscordAPI(token="token", max_retries=0, retry_time_buffer=(0, 0))
    monkeypatch.setattr(api.session, "request", lambda **_: FakeResponse(500, {"retry_after": 0}))
    monkeypatch.setattr("delete_me_discord.api.time.sleep", lambda *_: None)
    with pytest.raises(ReachedMaxRetries):
        api._request("http://example.com", description="test", method="get")


def test_api_requires_token(monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    with pytest.raises(ValueError):
        DiscordAPI(token=None)


def test_api_simple_routes_use_request(monkeypatch):
    api = DiscordAPI(token="token", max_retries=0, retry_time_buffer=(0, 0))
    calls = []

    def fake_request(url, description, method="get", params=None):
        calls.append((url, description, method))
        return [{"id": "me"}]

    monkeypatch.setattr(api, "_request", fake_request)
    api.get_guilds()
    api.get_guild_channels("g1")
    api.get_root_channels()
    api.get_current_user()

    assert calls[0][0].endswith("/users/@me/guilds")
    assert calls[1][0].endswith("/guilds/g1/channels")
    assert calls[2][0].endswith("/users/@me/channels")
    assert calls[3][0].endswith("/users/@me")


def test_get_guild_channels_multiple_skips_unavailable(monkeypatch):
    api = DiscordAPI(token="token", max_retries=0, retry_time_buffer=(0, 0))

    def fake_get_guild_channels(guild_id):
        if guild_id == "bad":
            raise ResourceUnavailable("gone")
        return [{"id": f"{guild_id}-c"}]

    monkeypatch.setattr(api, "get_guild_channels", fake_get_guild_channels)
    channels = api.get_guild_channels_multiple(["good", "bad"])
    assert channels == [{"id": "good-c"}]


def test_delete_message_handles_unavailable(monkeypatch):
    api = DiscordAPI(token="token", max_retries=0, retry_time_buffer=(0, 0))
    monkeypatch.setattr(api, "_request", lambda *_, **__: (_ for _ in ()).throw(ResourceUnavailable("gone")))
    assert api.delete_message("c1", "m1") is False


def test_delete_own_reaction_handles_unavailable(monkeypatch):
    api = DiscordAPI(token="token", max_retries=0, retry_time_buffer=(0, 0))
    monkeypatch.setattr(api, "_request", lambda *_, **__: (_ for _ in ()).throw(ResourceUnavailable("gone")))
    assert api.delete_own_reaction("c1", "m1", emoji={"name": "x"}) is False


def test_format_emoji_identifier_name_only():
    api = DiscordAPI(token="token", max_retries=0, retry_time_buffer=(0, 0))
    assert api._format_emoji_identifier({"name": "wave"}) == "wave"
