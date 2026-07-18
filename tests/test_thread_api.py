import pytest

from delete_me_discord.discord.client import DiscordClient
from delete_me_discord.discord.errors import UnexpectedStatus


def test_search_channel_threads_fetches_active_threads(monkeypatch):
    api = DiscordClient(token="token")
    captured = {}

    def fake_request(url, description, method="get", params=None, pacing_policy="read"):
        captured.update(
            url=url,
            description=description,
            method=method,
            params=params,
            pacing_policy=pacing_policy,
        )
        return [{"threads": [{"id": "t1", "type": 11}], "members": []}]

    monkeypatch.setattr(api.transport, "request", fake_request)

    assert api.search_channel_threads("c1") == [{"id": "t1", "type": 11}]
    assert captured["url"].endswith("/channels/c1/threads/search")
    assert captured["params"] == {
        "limit": 25,
        "sort_by": "creation_time",
        "sort_order": "desc",
        "archived": "false",
    }
    assert captured["pacing_policy"] == "thread-search"


def test_search_channel_threads_paginates_by_thread_id_and_deduplicates(monkeypatch):
    api = DiscordClient(token="token")
    responses = [
        [{
            "threads": [{"id": "200", "type": 11}],
            "has_more": True,
        }],
        [{
            "threads": [
                {"id": "200", "type": 11},
                {"id": "100", "type": 12},
            ],
            "has_more": False,
        }],
    ]
    calls = []

    def fake_request(url, description, method="get", params=None, pacing_policy="read"):
        calls.append((url, dict(params or {})))
        assert pacing_policy == "thread-search"
        return responses.pop(0)

    monkeypatch.setattr(api.transport, "request", fake_request)

    threads = api.search_channel_threads("c1", include_archived=True)

    assert [thread["id"] for thread in threads] == ["200", "100"]
    assert calls[0][0].endswith("/channels/c1/threads/search")
    assert "archived" not in calls[0][1]
    assert calls[1][1]["max_id"] == "200"


def test_thread_search_pagination_stops_when_cursor_cannot_advance(monkeypatch, caplog):
    api = DiscordClient(token="token")
    monkeypatch.setattr(
        api.transport,
        "request",
        lambda *_, **__: [{"threads": [{"id": "t1", "type": 11}], "has_more": True}],
    )

    assert api.search_channel_threads("c1") == [{"id": "t1", "type": 11}]
    assert "cursor did not advance" in caplog.text


@pytest.mark.parametrize("response", [[], [{"wat": []}], [{"threads": "bad"}]])
def test_thread_collection_rejects_malformed_payload(monkeypatch, response):
    api = DiscordClient(token="token")
    monkeypatch.setattr(api.transport, "request", lambda *_, **__: response)

    with pytest.raises(UnexpectedStatus, match="Malformed"):
        api.search_channel_threads("c1")
