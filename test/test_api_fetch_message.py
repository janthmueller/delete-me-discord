import logging
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from delete_me_discord.api import DiscordAPI
from delete_me_discord.utils import AuthenticationError, ResourceUnavailable
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


def test_fetch_message_by_id_returns_none_on_unavailable():
    channel_id = "123"
    message_id = "456"
    session = FakeSession(FakeResponse(403, None))
    api = make_api_with_session(session)
    assert api.fetch_message_by_id(channel_id=channel_id, message_id=message_id) is None


def test_fetch_messages_paginates_and_respects_before(monkeypatch):
    api = DiscordAPI(token="dummy-token")
    calls = []
    responses = [
        [
            {"id": "200", "timestamp": "2026-01-02T00:00:00.000000+00:00", "type": 0, "author": {"id": "u1"}, "reactions": []},
            {"id": "150", "timestamp": "2026-01-01T00:00:00.000000+00:00", "type": 0, "author": {"id": "u1"}, "reactions": []},
        ],
        [],
    ]

    def fake_request(url, description, params=None, method="get"):
        calls.append(params or {})
        return responses.pop(0)

    monkeypatch.setattr(api, "_request", fake_request)
    monkeypatch.setattr("delete_me_discord.api.time.sleep", lambda *_: None)

    messages = list(api.fetch_messages(channel_id="c1", fetch_sleep_time_range=(0, 0)))
    assert [m["message_id"] for m in messages] == ["200", "150"]
    assert calls[0] == {"limit": 100}
    assert calls[1]["before"] == "150"


@pytest.mark.parametrize(
    ("value", "name", "deletable"),
    [
        (0, "DEFAULT", True),
        (1, "RECIPIENT_ADD", False),
        (2, "RECIPIENT_REMOVE", False),
        (3, "CALL", False),
        (4, "CHANNEL_NAME_CHANGE", False),
        (5, "CHANNEL_ICON_CHANGE", False),
        (6, "CHANNEL_PINNED_MESSAGE", True),
        (7, "USER_JOIN", True),
        (8, "PREMIUM_GUILD_SUBSCRIPTION", True),
        (9, "PREMIUM_GUILD_SUBSCRIPTION_TIER_1", True),
        (10, "PREMIUM_GUILD_SUBSCRIPTION_TIER_2", True),
        (11, "PREMIUM_GUILD_SUBSCRIPTION_TIER_3", True),
        (12, "CHANNEL_FOLLOW_ADD", True),
        (14, "GUILD_DISCOVERY_DISQUALIFIED", True),
        (15, "GUILD_DISCOVERY_REQUALIFIED", True),
        (16, "GUILD_DISCOVERY_GRACE_PERIOD_INITIAL_WARNING", True),
        (17, "GUILD_DISCOVERY_GRACE_PERIOD_FINAL_WARNING", True),
        (18, "THREAD_CREATED", True),
        (19, "REPLY", True),
        (20, "CHAT_INPUT_COMMAND", True),
        (21, "THREAD_STARTER_MESSAGE", False),
        (22, "GUILD_INVITE_REMINDER", True),
        (23, "CONTEXT_MENU_COMMAND", True),
        (24, "AUTO_MODERATION_ACTION", False),
        (25, "ROLE_SUBSCRIPTION_PURCHASE", True),
        (26, "INTERACTION_PREMIUM_UPSELL", True),
        (27, "STAGE_START", True),
        (28, "STAGE_END", True),
        (29, "STAGE_SPEAKER", True),
        (30, "STAGE_RAISE_HAND", True),
        (31, "STAGE_TOPIC", True),
        (32, "GUILD_APPLICATION_PREMIUM_SUBSCRIPTION", True),
        (35, "PREMIUM_REFERRAL", False),
        (36, "GUILD_INCIDENT_ALERT_MODE_ENABLED", True),
        (37, "GUILD_INCIDENT_ALERT_MODE_DISABLED", True),
        (38, "GUILD_INCIDENT_REPORT_RAID", True),
        (39, "GUILD_INCIDENT_REPORT_FALSE_ALARM", True),
        (40, "GUILD_DEADCHAT_REVIVE_PROMPT", True),
        (41, "CUSTOM_GIFT", True),
        (42, "GUILD_GAMING_STATS_PROMPT", True),
        (44, "PURCHASE_NOTIFICATION", True),
        (46, "POLL_RESULT", True),
        (47, "CHANGELOG", True),
        (48, "NITRO_NOTIFICATION", True),
        (49, "CHANNEL_LINKED_TO_LOBBY", True),
        (50, "GIFTING_PROMPT", True),
        (51, "IN_GAME_MESSAGE_NUX", True),
        (52, "GUILD_JOIN_REQUEST_ACCEPT_NOTIFICATION", True),
        (53, "GUILD_JOIN_REQUEST_REJECT_NOTIFICATION", True),
        (54, "GUILD_JOIN_REQUEST_WITHDRAWN_NOTIFICATION", True),
        (55, "HD_STREAMING_UPGRADED", True),
        (58, "REPORT_TO_MOD_DELETED_MESSAGE", True),
        (59, "REPORT_TO_MOD_TIMEOUT_USER", True),
        (60, "REPORT_TO_MOD_KICK_USER", True),
        (61, "REPORT_TO_MOD_BAN_USER", True),
        (62, "REPORT_TO_MOD_CLOSED_REPORT", True),
        (63, "EMOJI_ADDED", True),
        (64, "PREMIUM_GROUP_INVITE", False),
        (65, "VOICE_SESSION", True),
        (66, "GUILD_BOOST_UPSELL", True),
        (67, "FRIEND_REQUEST_ACCEPTED", True),
        (68, "MEDIA_MENTION_MESSAGE", True),
    ],
)
def test_message_type_mapping(value, name, deletable):
    message_type = MessageType(value)

    assert message_type.name == name
    assert message_type.deletable is deletable


def test_fetch_messages_maps_friend_request_accepted_type(monkeypatch):
    api = DiscordAPI(token="dummy-token")
    responses = [
        [
            {
                "id": "200",
                "timestamp": "2026-01-02T00:00:00.000000+00:00",
                "type": 67,
                "author": {"id": "u1"},
                "reactions": [],
            }
        ],
        [],
    ]

    monkeypatch.setattr(api, "_request", lambda *_, **__: responses.pop(0))

    message = list(api.fetch_messages(channel_id="c1", fetch_sleep_time_range=(0, 0)))[0]

    assert message["type"] is MessageType.FRIEND_REQUEST_ACCEPTED
    assert message["type"].deletable is True


def test_unknown_numeric_message_type_warns_and_remains_non_deletable(caplog):
    with caplog.at_level(logging.WARNING, logger="MessageType"):
        message_type = MessageType(999)
        MessageType(999)

    assert message_type.name == "UNKNOWN_999"
    assert message_type.deletable is False
    warnings = [record for record in caplog.records if "unrecognized message type 999" in record.message]
    assert len(warnings) == 2


def test_fetch_messages_stops_at_max_messages(monkeypatch):
    api = DiscordAPI(token="dummy-token")
    responses = [
        [
            {"id": "200", "timestamp": "2026-01-02T00:00:00.000000+00:00", "type": 0, "author": {"id": "u1"}, "reactions": []},
            {"id": "150", "timestamp": "2026-01-01T00:00:00.000000+00:00", "type": 0, "author": {"id": "u1"}, "reactions": []},
        ],
    ]

    def fake_request(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(api, "_request", fake_request)
    messages = list(api.fetch_messages(channel_id="c1", max_messages=1, fetch_sleep_time_range=(0, 0)))
    assert [m["message_id"] for m in messages] == ["200"]


def test_fetch_messages_stops_at_cutoff(monkeypatch):
    api = DiscordAPI(token="dummy-token")
    responses = [
        [
            {"id": "200", "timestamp": "2026-01-02T00:00:00.000000+00:00", "type": 0, "author": {"id": "u1"}, "reactions": []},
            {"id": "150", "timestamp": "2025-12-31T00:00:00.000000+00:00", "type": 0, "author": {"id": "u1"}, "reactions": []},
        ],
        [{"id": "100", "timestamp": "2025-12-30T00:00:00.000000+00:00", "type": 0, "author": {"id": "u1"}, "reactions": []}],
    ]

    def fake_request(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(api, "_request", fake_request)
    cutoff = datetime.fromisoformat("2026-01-01T12:00:00+00:00")
    messages = list(api.fetch_messages(channel_id="c1", fetch_since=cutoff, fetch_sleep_time_range=(0, 0)))
    assert [m["message_id"] for m in messages] == ["200"]


def test_fetch_messages_handles_unavailable_channel(monkeypatch):
    api = DiscordAPI(token="dummy-token")
    monkeypatch.setattr(api, "_request", lambda *_, **__: (_ for _ in ()).throw(ResourceUnavailable("gone")))
    messages = list(api.fetch_messages(channel_id="c1", fetch_sleep_time_range=(0, 0)))
    assert messages == []
