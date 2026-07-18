# delete-me-discord api behavior tests
import sys
from pathlib import Path

import pytest
import requests

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.discord.client import DiscordClient
from delete_me_discord.discord.models import DeleteOutcome
from delete_me_discord.discord.rate_limits import DiscordRequestScheduler
from delete_me_discord.discord.type_enums import ReactionType
from delete_me_discord.discord.errors import (
    AuthenticationError,
    ReachedMaxRetries,
    ResourceUnavailable,
    UnexpectedStatus,
)
from delete_me_discord.discord.transport import DiscordTransport
from delete_me_discord.utils import DIAGNOSTIC_LEVEL


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def scheduler_for(clock):
    return DiscordRequestScheduler(
        retry_jitter=(0, 0),
        default_interval=(0, 0),
        clock=clock,
        sleeper=clock.sleep,
        random_between=lambda _minimum, maximum: maximum,
    )


def test_client_uses_injected_transport_without_resolving_a_token(monkeypatch):
    transport = DiscordTransport(token="transport-token")
    closed = []
    monkeypatch.setattr(
        transport,
        "request",
        lambda url, description, **_: [{"url": url, "description": description}],
    )
    monkeypatch.setattr(transport, "close", lambda: closed.append(True))
    client = DiscordClient(transport=transport)

    assert client.get_guilds() == [
        {
            "url": f"{client.BASE_URL}/users/@me/guilds",
            "description": "fetch guilds",
        }
    ]
    client.close()
    assert closed == [True]


def test_transport_rejects_unsupported_http_method():
    transport = DiscordTransport(token="token")

    with pytest.raises(ValueError, match="Unsupported Discord HTTP method"):
        transport.request(
            "http://example.com",
            description="post data",
            method="post",
        )


def test_request_retries_on_rate_limit_then_success(monkeypatch, caplog):
    caplog.set_level(DIAGNOSTIC_LEVEL)
    clock = FakeClock()
    api = DiscordClient(
        token="token",
        max_retries=2,
        retry_time_buffer=(0, 0),
        request_scheduler=scheduler_for(clock),
    )
    responses = [
        FakeResponse(429, {"retry_after": 2}),
        FakeResponse(200, [{"ok": True}]),
    ]

    def fake_request(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(api.transport.session, "request", fake_request)

    data = api.transport.request("http://example.com", description="test", method="get")
    assert data == [{"ok": True}]
    assert clock.sleeps == [2.0]
    assert "Discord retry 2.00 seconds + 0.00 seconds safety" in caplog.text
    assert "scope=route, policy=read" in caplog.text
    assert all(record.levelno == DIAGNOSTIC_LEVEL for record in caplog.records)


def test_request_retries_network_error_with_route_backoff(monkeypatch, caplog):
    caplog.set_level(DIAGNOSTIC_LEVEL)
    clock = FakeClock()
    api = DiscordClient(
        token="token",
        max_retries=1,
        request_scheduler=scheduler_for(clock),
    )
    responses = iter(
        [requests.RequestException("offline"), FakeResponse(200, {"ok": True})]
    )

    def fake_request(**_kwargs):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(api.transport.session, "request", fake_request)

    assert api.transport.request("http://example.com", description="test") == [{"ok": True}]
    assert clock.sleeps == [1.0]
    assert "Network error" in caplog.text
    assert all(record.levelno == DIAGNOSTIC_LEVEL for record in caplog.records)


def test_request_retries_server_error_using_retry_after_header(monkeypatch, caplog):
    caplog.set_level(DIAGNOSTIC_LEVEL)
    clock = FakeClock()
    api = DiscordClient(
        token="token",
        max_retries=1,
        request_scheduler=scheduler_for(clock),
    )
    responses = iter(
        [
            FakeResponse(503, {}, {"Retry-After": "2.5"}),
            FakeResponse(200, []),
        ]
    )
    monkeypatch.setattr(api.transport.session, "request", lambda **_: next(responses))

    assert api.transport.request("http://example.com", description="test") == []
    assert clock.sleeps == [2.5]
    assert "Retryable HTTP 503" in caplog.text
    assert all(record.levelno == DIAGNOSTIC_LEVEL for record in caplog.records)


def test_request_retries_http_408_with_full_jitter(monkeypatch, caplog):
    caplog.set_level(DIAGNOSTIC_LEVEL)
    clock = FakeClock()
    api = DiscordClient(
        token="token",
        max_retries=1,
        request_scheduler=scheduler_for(clock),
    )
    responses = iter([FakeResponse(408, {}), FakeResponse(200, [])])
    monkeypatch.setattr(api.transport.session, "request", lambda **_: next(responses))

    assert api.transport.request("http://example.com", description="test") == []
    assert clock.sleeps == [1.0]
    assert "Retryable HTTP 408" in caplog.text


def test_delete_reports_absent_when_retry_finds_prior_attempt_already_applied(
    monkeypatch,
):
    clock = FakeClock()
    api = DiscordClient(
        token="token",
        max_retries=1,
        request_scheduler=scheduler_for(clock),
    )
    responses = iter([
        requests.ReadTimeout("response lost"),
        FakeResponse(404, {"message": "Unknown Message"}),
    ])

    def fake_request(**_kwargs):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(api.transport.session, "request", fake_request)

    assert api.delete_message("c1", "m1") == DeleteOutcome.ABSENT
    assert clock.sleeps == [2.0]


def test_request_uses_exponential_full_jitter_for_hintless_server_errors(
    monkeypatch,
    caplog,
):
    caplog.set_level(DIAGNOSTIC_LEVEL)
    clock = FakeClock()
    api = DiscordClient(
        token="token",
        max_retries=2,
        request_scheduler=scheduler_for(clock),
    )
    responses = iter([
        FakeResponse(500, {}),
        FakeResponse(502, {}),
        FakeResponse(200, []),
    ])
    monkeypatch.setattr(api.transport.session, "request", lambda **_: next(responses))

    assert api.transport.request("http://example.com", description="test") == []
    assert clock.sleeps == [1.0, 2.0]
    assert "Retryable HTTP 500" in caplog.text
    assert "Retryable HTTP 502" in caplog.text


def test_request_uses_exponential_full_jitter_for_hintless_rate_limits(
    monkeypatch,
    caplog,
):
    caplog.set_level(DIAGNOSTIC_LEVEL)
    clock = FakeClock()
    api = DiscordClient(
        token="token",
        max_retries=2,
        request_scheduler=scheduler_for(clock),
    )
    responses = iter([
        FakeResponse(429, {}),
        FakeResponse(429, {}),
        FakeResponse(200, []),
    ])
    monkeypatch.setattr(api.transport.session, "request", lambda **_: next(responses))

    assert api.transport.request("http://example.com", description="test") == []
    assert clock.sleeps == [1.0, 2.0]
    assert caplog.text.count("without a usable Discord retry delay") == 2
    assert caplog.text.count("Full-jitter fallback") == 2


def test_request_retries_while_discord_builds_search_index(monkeypatch, caplog):
    caplog.set_level(DIAGNOSTIC_LEVEL)
    clock = FakeClock()
    api = DiscordClient(
        token="token",
        max_retries=1,
        retry_time_buffer=(0, 0),
        request_scheduler=scheduler_for(clock),
    )
    responses = iter(
        [
            FakeResponse(
                202,
                {
                    "message": "Index not yet available. Try again later",
                    "code": 110000,
                    "retry_after": 2,
                },
            ),
            FakeResponse(200, {"threads": [], "has_more": False}),
        ]
    )
    monkeypatch.setattr(api.transport.session, "request", lambda **_: next(responses))

    assert api.transport.request("http://example.com", description="search threads") == [
        {"threads": [], "has_more": False}
    ]
    assert clock.sleeps == [2.0]
    assert "indexing data" in caplog.text
    assert all(record.levelno == DIAGNOSTIC_LEVEL for record in caplog.records)


def test_request_passes_connect_and_read_timeout(monkeypatch):
    api = DiscordClient(token="token", request_timeout=(2.5, 8.0))
    captured = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return FakeResponse(200, [])

    monkeypatch.setattr(api.transport.session, "request", fake_request)

    api.transport.request("http://example.com", description="test")

    assert captured["timeout"] == (2.5, 8.0)


def test_delete_pacing_happens_before_the_next_delete(monkeypatch):
    clock = FakeClock()
    api = DiscordClient(
        token="token",
        request_scheduler=scheduler_for(clock),
    )
    api.configure_request_policy("delete", (1.25, 1.25))
    monkeypatch.setattr(api.transport.session, "request", lambda **_: FakeResponse(204))

    assert api.delete_message("c1", "m1") == DeleteOutcome.DELETED
    assert clock.sleeps == []

    assert api.delete_message("c2", "m2") == DeleteOutcome.DELETED
    assert clock.sleeps == [1.25]


def test_explicit_policy_override_wins_over_cleaner_configuration(monkeypatch):
    clock = FakeClock()
    api = DiscordClient(
        token="token",
        request_intervals={"delete": (3.0, 3.0)},
        request_scheduler=scheduler_for(clock),
    )
    api.configure_request_policy("delete", (1.0, 1.0))
    monkeypatch.setattr(api.transport.session, "request", lambda **_: FakeResponse(204))

    assert api.delete_message("c1", "m1") == DeleteOutcome.DELETED
    assert api.delete_message("c2", "m2") == DeleteOutcome.DELETED

    assert clock.sleeps == [3.0]


def test_request_does_not_sleep_when_no_retries_remain(monkeypatch):
    clock = FakeClock()
    api = DiscordClient(
        token="token",
        max_retries=0,
        retry_time_buffer=(0, 0),
        request_scheduler=scheduler_for(clock),
    )
    monkeypatch.setattr(api.transport.session, "request", lambda **_: FakeResponse(500, {}))

    with pytest.raises(ReachedMaxRetries):
        api.transport.request("http://example.com", description="test")

    assert clock.sleeps == []


def test_request_raises_on_unauthorized_and_unavailable(monkeypatch):
    api = DiscordClient(token="token", max_retries=0, retry_time_buffer=(0, 0))

    monkeypatch.setattr(api.transport.session, "request", lambda **_: FakeResponse(401, {}))
    with pytest.raises(AuthenticationError):
        api.transport.request("http://example.com", description="test", method="get")

    monkeypatch.setattr(api.transport.session, "request", lambda **_: FakeResponse(403, {}))
    with pytest.raises(ResourceUnavailable):
        api.transport.request("http://example.com", description="test", method="get")


def test_request_unexpected_status_for_delete(monkeypatch):
    api = DiscordClient(token="token", max_retries=0, retry_time_buffer=(0, 0))
    monkeypatch.setattr(api.transport.session, "request", lambda **_: FakeResponse(200, {}))
    with pytest.raises(UnexpectedStatus):
        api.transport.request("http://example.com", description="delete", method="delete")


def test_delete_own_reaction_requires_emoji_identifier(monkeypatch):
    api = DiscordClient(token="token", max_retries=0, retry_time_buffer=(0, 0))
    monkeypatch.setattr(api.transport, "request", lambda *_, **__: (_ for _ in ()).throw(AssertionError("should not call")))
    assert api.delete_own_reaction("c1", "m1", emoji={}) == DeleteOutcome.FAILED


def test_delete_own_reaction_encodes_identifier(monkeypatch):
    api = DiscordClient(token="token", max_retries=0, retry_time_buffer=(0, 0))
    captured = {}

    def fake_request(url, description, method="get", params=None, pacing_policy="read"):
        captured["url"] = url
        captured["method"] = method
        captured["pacing_policy"] = pacing_policy
        return []

    monkeypatch.setattr(api.transport, "request", fake_request)
    assert (
        api.delete_own_reaction("c1", "m1", emoji={"name": "smile", "id": "123"})
        == DeleteOutcome.DELETED
    )
    assert captured["method"] == "delete"
    assert captured["pacing_policy"] == "delete"
    assert "/reactions/smile%3A123/@me" in captured["url"]


def test_delete_own_reaction_rejects_unknown_type(monkeypatch):
    api = DiscordClient(token="token", max_retries=0, retry_time_buffer=(0, 0))
    monkeypatch.setattr(
        api.transport,
        "request",
        lambda *_, **__: (_ for _ in ()).throw(AssertionError("should not call")),
    )

    assert (
        api.delete_own_reaction("c1", "m1", {"name": "x"}, reaction_type=9)
        == DeleteOutcome.FAILED
    )
    assert (
        api.delete_own_reaction(
            "c1",
            "m1",
            {"name": "x"},
            reaction_type=None,
        )
        == DeleteOutcome.FAILED
    )


def test_delete_own_reaction_accepts_integer_burst_type(monkeypatch):
    api = DiscordClient(token="token", max_retries=0, retry_time_buffer=(0, 0))
    captured = {}

    def fake_request(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs["params"]
        return []

    monkeypatch.setattr(api.transport, "request", fake_request)

    assert (
        api.delete_own_reaction("c1", "m1", {"name": "x"}, ReactionType.BURST)
        == DeleteOutcome.DELETED
    )
    assert captured["url"].endswith("/reactions/x/@me/1")
    assert captured["params"] == {"burst": True}


def test_request_reaches_max_retries_on_network_error(monkeypatch):
    api = DiscordClient(token="token", max_retries=0, retry_time_buffer=(0, 0))

    def boom(*args, **kwargs):
        raise requests.RequestException("offline")

    monkeypatch.setattr(api.transport.session, "request", boom)

    with pytest.raises(ReachedMaxRetries):
        api.transport.request("http://example.com", description="test", method="get")


def test_request_reaches_max_retries_on_500(monkeypatch):
    api = DiscordClient(token="token", max_retries=0, retry_time_buffer=(0, 0))
    monkeypatch.setattr(api.transport.session, "request", lambda **_: FakeResponse(500, {"retry_after": 0}))
    with pytest.raises(ReachedMaxRetries):
        api.transport.request("http://example.com", description="test", method="get")


def test_api_requires_token(monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    with pytest.raises(ValueError, match="Set the DISCORD_TOKEN environment variable"):
        DiscordClient(token=None)


def test_api_simple_routes_use_request(monkeypatch):
    api = DiscordClient(token="token", max_retries=0, retry_time_buffer=(0, 0))
    calls = []

    def fake_request(url, description, method="get", params=None):
        calls.append((url, description, method))
        return [{"id": "me"}]

    monkeypatch.setattr(api.transport, "request", fake_request)
    api.get_guilds()
    api.get_guild_channels("g1")
    api.get_root_channels()
    api.get_current_user()

    assert calls[0][0].endswith("/users/@me/guilds")
    assert calls[1][0].endswith("/guilds/g1/channels")
    assert calls[2][0].endswith("/users/@me/channels")
    assert calls[3][0].endswith("/users/@me")


def test_get_guild_channels_multiple_skips_unavailable(monkeypatch):
    api = DiscordClient(token="token", max_retries=0, retry_time_buffer=(0, 0))

    def fake_get_guild_channels(guild_id):
        if guild_id == "bad":
            raise ResourceUnavailable("gone")
        return [{"id": f"{guild_id}-c"}]

    monkeypatch.setattr(api, "get_guild_channels", fake_get_guild_channels)
    channels = api.get_guild_channels_multiple(["good", "bad"])
    assert channels == [{"id": "good-c"}]


def test_delete_message_handles_unavailable(monkeypatch):
    api = DiscordClient(token="token", max_retries=0, retry_time_buffer=(0, 0))
    monkeypatch.setattr(api.transport, "request", lambda *_, **__: (_ for _ in ()).throw(ResourceUnavailable("gone")))
    assert api.delete_message("c1", "m1") == DeleteOutcome.FAILED


def test_delete_own_reaction_handles_unavailable(monkeypatch):
    api = DiscordClient(token="token", max_retries=0, retry_time_buffer=(0, 0))
    monkeypatch.setattr(api.transport, "request", lambda *_, **__: (_ for _ in ()).throw(ResourceUnavailable("gone")))
    assert (
        api.delete_own_reaction("c1", "m1", emoji={"name": "x"})
        == DeleteOutcome.FAILED
    )


def test_format_emoji_identifier_name_only():
    api = DiscordClient(token="token", max_retries=0, retry_time_buffer=(0, 0))
    assert api._format_emoji_identifier({"name": "wave"}) == "wave"
