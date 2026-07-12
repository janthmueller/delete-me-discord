import pytest

from delete_me_discord.api import DiscordAPI
from delete_me_discord.models import DeleteOutcome
from delete_me_discord.privacy import RedactionConfig, set_redaction_config
from delete_me_discord.rate_limits import DiscordRequestScheduler
from delete_me_discord.type_enums import ReactionType
from delete_me_discord.utils import (
    AuthenticationError,
    ResourceUnavailable,
    UnexpectedStatus,
    ReachedMaxRetries,
)


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        # responses can be a list (iterated) or single response
        if isinstance(responses, list):
            self.responses = responses
        else:
            self.responses = [responses]
        self.calls = 0
        self.last_params = None
        self.last_url = None
        self.last_method = None
        self.last_timeout = None

    def request(self, method, url, params=None, timeout=None):
        self.calls += 1
        self.last_method = method
        self.last_url = url
        self.last_params = params or {}
        self.last_timeout = timeout
        try:
            response = self.responses[self.calls - 1]
        except IndexError:
            response = self.responses[-1]
        return response


def make_api(fake_session, max_retries=2):
    api = DiscordAPI(
        token="dummy",
        max_retries=max_retries,
        request_scheduler=DiscordRequestScheduler(
            retry_jitter=(0, 0),
            default_interval=(0, 0),
        ),
    )
    api.session = fake_session
    return api


def test_request_raises_authentication_error():
    session = FakeSession(FakeResponse(401, None))
    api = make_api(session)
    with pytest.raises(AuthenticationError):
        list(api.fetch_messages(channel_id="c1", max_messages=1))


def test_request_raises_resource_unavailable():
    session = FakeSession(FakeResponse(403, {"error": "forbidden"}))
    api = make_api(session)
    with pytest.raises(ResourceUnavailable) as exc_info:
        api.get_guilds()

    assert exc_info.value.status_code == 403


def test_get_channel_uses_exact_channel_route():
    channel_id = "123456789012345678"
    session = FakeSession(
        FakeResponse(
            200,
            {"id": channel_id, "type": 4, "guild_id": "223456789012345678"},
        )
    )
    api = make_api(session)

    channel = api.get_channel(channel_id)

    assert channel["id"] == channel_id
    assert session.last_method == "get"
    assert session.last_url.endswith(f"/channels/{channel_id}")


def test_get_channel_rejects_malformed_collection_response():
    api = make_api(FakeSession(FakeResponse(200, [])))

    with pytest.raises(UnexpectedStatus, match="Malformed channel response"):
        api.get_channel("123456789012345678")


def test_request_raises_unexpected_status():
    session = FakeSession(FakeResponse(418, {"error": "teapot"}))
    api = make_api(session)
    with pytest.raises(UnexpectedStatus):
        api.get_guilds()


def test_request_retries_and_hits_max():
    # Two 429s at max_retries=2 -> two retries, then fail
    responses = [
        FakeResponse(429, {"retry_after": 0}),
        FakeResponse(429, {"retry_after": 0}),
        FakeResponse(429, {"retry_after": 0}),
    ]
    api = make_api(FakeSession(responses), max_retries=2)
    with pytest.raises(ReachedMaxRetries):
        api.get_guilds()
    assert api.session.calls == 3  # initial + two retries


def test_fetch_messages_skips_on_resource_unavailable():
    # First call returns 403 -> ResourceUnavailable -> generator stops
    session = FakeSession(FakeResponse(403, {"error": "forbidden"}))
    api = make_api(session)
    messages = list(api.fetch_messages(channel_id="c1", max_messages=5))
    assert messages == []
    assert session.calls == 1
    assert api.get_last_fetch_summary("c1")["complete"] is False


def test_delete_own_reaction_encodes_identifier_in_url(caplog):
    session = FakeSession(FakeResponse(204, None))
    api = make_api(session)
    caplog.set_level("WARNING")
    set_redaction_config(RedactionConfig(enabled=True, prefix=0, suffix=4))
    try:
        result = api.delete_own_reaction(
            channel_id="123456789012345678",
            message_id="123456789012345679",
            emoji={"name": "sample_emoji", "id": "999999"},
        )
    finally:
        set_redaction_config(RedactionConfig())
    assert result == DeleteOutcome.DELETED
    assert session.last_url.endswith("/reactions/sample_emoji%3A999999/@me")


def test_delete_super_reaction_uses_typed_route_and_burst_query():
    session = FakeSession(FakeResponse(204, None))
    api = make_api(session)

    result = api.delete_own_reaction(
        channel_id="c1",
        message_id="m1",
        emoji={"name": "sparkles"},
        reaction_type=ReactionType.BURST,
    )

    assert result == DeleteOutcome.DELETED
    assert session.last_url.endswith("/reactions/sparkles/@me/1")
    assert session.last_params == {"burst": True}


def test_delete_reaction_formats_deleted_custom_emoji_for_discord_route():
    session = FakeSession(FakeResponse(204, None))
    api = make_api(session)

    result = api.delete_own_reaction(
        channel_id="c1",
        message_id="m1",
        emoji={"name": None, "id": "999999"},
        reaction_type=ReactionType.BURST,
    )

    assert result == DeleteOutcome.DELETED
    assert session.last_url.endswith("/reactions/null%3A999999/@me/1")


def test_delete_thread_accepts_channel_response():
    session = FakeSession(FakeResponse(200, {"id": "thread-1"}))
    api = make_api(session)

    assert api.delete_thread("thread-1") == DeleteOutcome.DELETED
    assert session.last_method == "delete"
    assert session.last_url.endswith("/channels/thread-1")


def test_delete_thread_handles_missing_permission():
    session = FakeSession(FakeResponse(403, {"message": "Missing Permissions"}))
    api = make_api(session)

    assert api.delete_thread("thread-1") == DeleteOutcome.FAILED


def test_delete_endpoints_report_absent_on_404():
    message_api = make_api(FakeSession(FakeResponse(404, {"message": "Unknown Message"})))
    reaction_api = make_api(FakeSession(FakeResponse(404, {"message": "Unknown Message"})))
    thread_api = make_api(FakeSession(FakeResponse(404, {"message": "Unknown Channel"})))

    assert message_api.delete_message("c1", "m1") == DeleteOutcome.ABSENT
    assert (
        reaction_api.delete_own_reaction("c1", "m1", {"name": "wave"})
        == DeleteOutcome.ABSENT
    )
    assert thread_api.delete_thread("thread-1") == DeleteOutcome.ABSENT


def test_delete_own_reaction_malformed_emoji_log_is_redacted(caplog):
    session = FakeSession(FakeResponse(204, None))
    api = make_api(session)
    caplog.set_level("WARNING")
    set_redaction_config(RedactionConfig(enabled=True, prefix=0, suffix=4))
    try:
        result = api.delete_own_reaction(channel_id="c1", message_id="m1", emoji={})
    finally:
        set_redaction_config(RedactionConfig())
    assert result == DeleteOutcome.FAILED
    assert any("missing emoji identifier" in rec.message for rec in caplog.records)


def test_delete_own_reaction_unavailable_log_redacts_emoji_and_ids(monkeypatch, caplog):
    api = DiscordAPI(token="token", max_retries=0, retry_time_buffer=(0, 0))
    monkeypatch.setattr(api, "_request", lambda *_, **__: (_ for _ in ()).throw(ResourceUnavailable("gone")))
    caplog.set_level("WARNING")
    set_redaction_config(RedactionConfig(enabled=True, prefix=0, suffix=4))
    try:
        result = api.delete_own_reaction(
            "123456789012345678",
            "123456789012345679",
            emoji={"name": "sample_emoji"},
        )
    finally:
        set_redaction_config(RedactionConfig())

    assert result == DeleteOutcome.FAILED
    assert "Skipping deletion of reaction *** from message ***5679 in channel ***5678 (unavailable: gone)." in caplog.text
    assert "sample_emoji" not in caplog.text
    assert "123456789012345678" not in caplog.text
    assert "123456789012345679" not in caplog.text
