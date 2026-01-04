import pytest

from delete_me_discord.api import DiscordAPI
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

    def request(self, method, url, params=None):
        self.calls += 1
        self.last_method = method
        self.last_url = url
        self.last_params = params or {}
        try:
            response = self.responses[self.calls - 1]
        except IndexError:
            response = self.responses[-1]
        return response


def make_api(fake_session, max_retries=2):
    api = DiscordAPI(token="dummy", max_retries=max_retries)
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
    with pytest.raises(ResourceUnavailable):
        api.get_guilds()


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


def test_delete_own_reaction_returns_false_on_malformed_emoji(caplog):
    session = FakeSession(FakeResponse(204, None))
    api = make_api(session)
    caplog.set_level("WARNING")
    result = api.delete_own_reaction(channel_id="c1", message_id="m1", emoji={})
    assert result is False
    assert any("missing emoji identifier" in rec.message for rec in caplog.records)
