import sys
from pathlib import Path

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from delete_me_discord.api import DiscordAPI
from delete_me_discord.utils import AuthenticationError
from delete_me_discord.type_enums import MessageType


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.last_url = None
        self.last_params = None
        self.last_method = None

    def request(self, method, url, params=None, json=None):
        self.last_method = method
        self.last_url = url
        self.last_params = params or {}
        return self.response


def make_api_with_session(fake_session):
    api = DiscordAPI(token="dummy-token")
    api.session = fake_session
    return api


def test_fetch_message_by_id_uses_around_cursor_and_maps_fields():
    channel_id = "123"
    message_id = "999"
    payload = [
        {
            "id": message_id,
            "timestamp": "2026-01-02T00:00:00.000000+00:00",
            "type": MessageType.DEFAULT.value,
            "author": {"id": "user-1"},
            "reactions": [{"emoji": {"name": "👍"}, "me": True}],
        }
    ]
    session = FakeSession(FakeResponse(200, payload))
    api = make_api_with_session(session)

    result = api.fetch_message_by_id(channel_id=channel_id, message_id=message_id)

    assert session.last_url.endswith(f"/channels/{channel_id}/messages")
    assert session.last_params == {"around": message_id, "limit": 1}
    assert result["message_id"] == message_id
    assert result["channel_id"] == channel_id
    assert result["author_id"] == "user-1"
    assert result["type"] == MessageType.DEFAULT
    assert result["reactions"] == payload[0]["reactions"]


def test_fetch_message_by_id_returns_none_on_mismatch():
    channel_id = "123"
    requested_id = "999"
    payload = [{"id": "different"}]
    session = FakeSession(FakeResponse(200, payload))
    api = make_api_with_session(session)

    result = api.fetch_message_by_id(channel_id=channel_id, message_id=requested_id)

    assert result is None


def test_fetch_message_by_id_returns_none_on_empty_response():
    channel_id = "123"
    message_id = "456"
    payload = []
    session = FakeSession(FakeResponse(200, payload))
    api = make_api_with_session(session)

    result = api.fetch_message_by_id(channel_id=channel_id, message_id=message_id)

    assert result is None
    assert session.last_params == {"around": message_id, "limit": 1}


def test_delete_message_succeeds_on_204_without_body():
    channel_id = "abc"
    message_id = "m1"
    session = FakeSession(FakeResponse(204, None))
    api = make_api_with_session(session)

    result = api.delete_message(channel_id=channel_id, message_id=message_id)

    assert result is True
    assert session.last_method == "delete"
    assert session.last_url.endswith(f"/channels/{channel_id}/messages/{message_id}")


def test_fetch_message_by_id_raises_on_401():
    channel_id = "123"
    message_id = "456"
    session = FakeSession(FakeResponse(401, None))
    api = make_api_with_session(session)

    with pytest.raises(AuthenticationError):
        api.fetch_message_by_id(channel_id=channel_id, message_id=message_id)
