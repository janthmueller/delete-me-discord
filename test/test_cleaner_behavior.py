# delete-me-discord cleaner behavior tests
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from delete_me_discord.cleaner import MessageCleaner


class DummyType:
    def __init__(self, deletable: bool):
        self.deletable = deletable


class DummyAPI:
    def delete_message(self, channel_id, message_id):
        return True

    def delete_own_reaction(self, channel_id, message_id, emoji):
        return True


def make_message(mid, author_id, dt, deletable=True, reactions=None):
    return {
        "message_id": mid,
        "timestamp": dt.isoformat().replace("+00:00", "Z"),
        "author_id": author_id,
        "type": DummyType(deletable),
        "channel_id": "c1",
        "reactions": reactions or [],
    }


def make_cleaner(preserve_n, preserve_n_mode):
    return MessageCleaner(
        api=DummyAPI(),
        user_id="me",
        preserve_last=timedelta(weeks=2),
        preserve_n=preserve_n,
        preserve_n_mode=preserve_n_mode,
    )


def test_delete_messages_preserve_n_mine():
    cleaner = make_cleaner(preserve_n=2, preserve_n_mode="mine")
    now = datetime.now(timezone.utc)
    messages = [
        make_message("1", "me", now - timedelta(days=10)),
        make_message("2", "me", now - timedelta(days=11)),
        make_message("3", "me", now - timedelta(days=12)),
        make_message("4", "me", now - timedelta(days=13)),
    ]
    preserved, stats, _ = cleaner.delete_messages_older_than(
        messages=iter(messages),
        cutoff_time=now,
        delete_sleep_time_range=(0, 0),
        dry_run=True,
        delete_reactions=False,
    )
    assert stats["preserved_deletable_count"] == 2
    assert stats["deleted_count"] == 2
    assert preserved == ["1", "2"]


def test_delete_messages_preserve_n_all_counts_non_deletable():
    cleaner = make_cleaner(preserve_n=2, preserve_n_mode="all")
    now = datetime.now(timezone.utc)
    messages = [
        make_message("1", "other", now - timedelta(days=10), deletable=False),
        make_message("2", "other", now - timedelta(days=11), deletable=False),
        make_message("3", "me", now - timedelta(days=12)),
        make_message("4", "me", now - timedelta(days=13)),
    ]
    preserved, stats, _ = cleaner.delete_messages_older_than(
        messages=iter(messages),
        cutoff_time=now,
        delete_sleep_time_range=(0, 0),
        dry_run=True,
        delete_reactions=False,
    )
    assert stats["preserved_deletable_count"] == 0
    assert stats["deleted_count"] == 2
    assert preserved == []


def test_delete_messages_reaction_removal_counts():
    cleaner = make_cleaner(preserve_n=0, preserve_n_mode="mine")
    now = datetime.now(timezone.utc)
    reactions = [
        {"me": True, "emoji": {"name": "x"}},
        {"me": True, "emoji": {"name": "y"}},
    ]
    messages = [
        make_message("1", "other", now - timedelta(days=10), deletable=False, reactions=reactions),
    ]
    _, stats, _ = cleaner.delete_messages_older_than(
        messages=iter(messages),
        cutoff_time=now,
        delete_sleep_time_range=(0, 0),
        dry_run=True,
        delete_reactions=True,
    )
    assert stats["reactions_removed_count"] == 2


def test_delete_messages_preserve_last_window():
    cleaner = make_cleaner(preserve_n=0, preserve_n_mode="mine")
    now = datetime.now(timezone.utc)
    messages = [
        make_message("1", "me", now - timedelta(days=1)),
        make_message("2", "me", now - timedelta(days=10)),
    ]
    preserved, stats, _ = cleaner.delete_messages_older_than(
        messages=iter(messages),
        cutoff_time=now - timedelta(days=5),
        delete_sleep_time_range=(0, 0),
        dry_run=True,
        delete_reactions=False,
    )
    assert preserved == ["1"]
    assert stats["preserved_deletable_count"] == 1
    assert stats["deleted_count"] == 1


def test_delete_messages_non_dry_run_deletes():
    class RecordingAPI:
        def __init__(self):
            self.deleted = []

        def delete_message(self, channel_id, message_id):
            self.deleted.append((channel_id, message_id))
            return True

        def delete_own_reaction(self, channel_id, message_id, emoji):
            return True

    api = RecordingAPI()
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        preserve_last=timedelta(weeks=2),
        preserve_n=0,
        preserve_n_mode="mine",
    )
    now = datetime.now(timezone.utc)
    messages = [
        make_message("1", "me", now - timedelta(days=10)),
    ]
    _, stats, _ = cleaner.delete_messages_older_than(
        messages=iter(messages),
        cutoff_time=now,
        delete_sleep_time_range=(0, 0),
        dry_run=False,
        delete_reactions=False,
    )
    assert stats["deleted_count"] == 1
    assert api.deleted == [("c1", "1")]


def test_delete_messages_reaction_removal_non_dry_run():
    class RecordingAPI:
        def __init__(self):
            self.reactions = []

        def delete_message(self, channel_id, message_id):
            return True

        def delete_own_reaction(self, channel_id, message_id, emoji):
            self.reactions.append((channel_id, message_id, emoji.get("name")))
            return True

    api = RecordingAPI()
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        preserve_last=timedelta(weeks=2),
        preserve_n=0,
        preserve_n_mode="mine",
    )
    now = datetime.now(timezone.utc)
    reactions = [{"me": True, "emoji": {"name": "x"}}]
    messages = [
        make_message("1", "other", now - timedelta(days=10), deletable=False, reactions=reactions),
    ]
    _, stats, _ = cleaner.delete_messages_older_than(
        messages=iter(messages),
        cutoff_time=now,
        delete_sleep_time_range=(0, 0),
        dry_run=False,
        delete_reactions=True,
    )
    assert stats["reactions_removed_count"] == 1
    assert api.reactions == [("c1", "1", "x")]


def test_clean_messages_merges_cached_ids(monkeypatch):
    class RecordingAPI:
        def __init__(self):
            self.fetched = []

        def fetch_message_by_id(self, channel_id, message_id):
            self.fetched.append(message_id)
            now = datetime.now(timezone.utc)
            return make_message(message_id, "me", now)

    class FakeCache:
        def __init__(self):
            self.set_calls = []

        def get_ids(self, channel_id):
            return ["90"]

        def set_ids(self, channel_id, message_ids):
            self.set_calls.append((channel_id, list(message_ids)))

    api = RecordingAPI()
    cache = FakeCache()
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        preserve_last=timedelta(weeks=2),
        preserve_n=0,
        preserve_n_mode="mine",
        preserve_cache=cache,
    )

    now = datetime.now(timezone.utc)
    main_messages = [make_message("100", "me", now)]

    monkeypatch.setattr(cleaner, "get_all_channels", lambda: [{"id": "c1", "type": 0, "name": "chan"}])
    monkeypatch.setattr(cleaner, "fetch_all_messages", lambda **_: iter(main_messages))

    deleted = cleaner.clean_messages(
        dry_run=True,
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
        fetch_since=None,
        max_messages=10,
        delete_reactions=False,
    )
    assert deleted == 0
    assert api.fetched == ["90"]
    assert cache.set_calls[0][0] == "c1"
    assert set(cache.set_calls[0][1]) == {"100", "90"}


def test_prepare_channel_messages_buffers_single_channel(monkeypatch):
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me")
    channel = {"id": "c1", "type": 0, "name": "chan"}
    now = datetime.now(timezone.utc)
    source_messages = [
        make_message("2", "me", now),
        make_message("1", "me", now - timedelta(seconds=1)),
    ]

    monkeypatch.setattr(cleaner, "fetch_all_messages", lambda **_: iter(source_messages))

    prepared, buffer_elapsed = cleaner._prepare_channel_messages(
        channel=channel,
        fetch_sleep_time_range=(0, 0),
        fetch_since=None,
        max_messages=10,
        buffer_channel_messages=True,
    )

    assert isinstance(prepared, list)
    assert [message["message_id"] for message in prepared] == ["2", "1"]
    assert isinstance(buffer_elapsed, float)


def test_clean_messages_buffered_mode_keeps_delete_behavior(monkeypatch):
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me")
    now = datetime.now(timezone.utc)
    messages = [
        make_message("1", "me", now - timedelta(days=20)),
        make_message("2", "me", now - timedelta(days=21)),
    ]

    monkeypatch.setattr(cleaner, "get_all_channels", lambda: [{"id": "c1", "type": 0, "name": "chan"}])
    monkeypatch.setattr(cleaner, "fetch_all_messages", lambda **_: iter(messages))

    total = cleaner.clean_messages(
        dry_run=True,
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
        fetch_since=None,
        max_messages=10,
        buffer_channel_messages=True,
        delete_reactions=False,
    )

    assert total == 2


def test_delete_messages_handles_delete_failure():
    class FailingAPI:
        def delete_message(self, channel_id, message_id):
            return False

        def delete_own_reaction(self, channel_id, message_id, emoji):
            return True

    cleaner = MessageCleaner(
        api=FailingAPI(),
        user_id="me",
        preserve_last=timedelta(weeks=2),
        preserve_n=0,
        preserve_n_mode="mine",
    )
    now = datetime.now(timezone.utc)
    messages = [
        make_message("1", "me", now - timedelta(days=10)),
    ]
    _, stats, _ = cleaner.delete_messages_older_than(
        messages=iter(messages),
        cutoff_time=now,
        delete_sleep_time_range=(0, 0),
        dry_run=False,
        delete_reactions=False,
    )
    assert stats["deleted_count"] == 0


def test_delete_reactions_handles_failure():
    class FailingAPI:
        def delete_message(self, channel_id, message_id):
            return True

        def delete_own_reaction(self, channel_id, message_id, emoji):
            return False

    cleaner = MessageCleaner(
        api=FailingAPI(),
        user_id="me",
        preserve_last=timedelta(weeks=2),
        preserve_n=0,
        preserve_n_mode="mine",
    )
    now = datetime.now(timezone.utc)
    reactions = [{"me": True, "emoji": {"name": "x"}}]
    messages = [
        make_message("1", "other", now - timedelta(days=10), deletable=False, reactions=reactions),
    ]
    _, stats, _ = cleaner.delete_messages_older_than(
        messages=iter(messages),
        cutoff_time=now,
        delete_sleep_time_range=(0, 0),
        dry_run=False,
        delete_reactions=True,
    )
    assert stats["reactions_removed_count"] == 0


def test_init_fetches_user_id_from_api():
    class Api:
        def get_current_user(self):
            return {"id": "user-1"}

    cleaner = MessageCleaner(api=Api())
    assert cleaner.user_id == "user-1"


def test_init_missing_user_id_raises():
    class Api:
        def get_current_user(self):
            raise RuntimeError("no token")

    with pytest.raises(ValueError):
        MessageCleaner(api=Api())


def test_init_include_exclude_overlap_raises():
    with pytest.raises(ValueError):
        MessageCleaner(api=DummyAPI(), user_id="me", include_ids=["a"], exclude_ids=["a"])


def test_get_all_channels_filters_unknown_types():
    class Api:
        def get_guilds(self):
            return [{"id": "g1"}]

        def get_guild_channels_multiple(self, guild_ids):
            return [
                {"id": "c1", "type": 0, "name": "chan"},
                {"id": "c2", "type": 99, "name": "unknown"},
            ]

        def get_root_channels(self):
            return [
                {"id": "dm1", "type": 1, "recipients": [{"username": "Amy"}]},
                {"id": "dm2", "type": 99, "recipients": [{"username": "Skip"}]},
            ]

    cleaner = MessageCleaner(api=Api(), user_id="me")
    channels = cleaner.get_all_channels()
    assert [c["id"] for c in channels] == ["dm1", "c1"]


def test_clean_messages_non_dry_run_summary(monkeypatch):
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me")
    monkeypatch.setattr(cleaner, "get_all_channels", lambda: [{"id": "c1", "type": 0, "name": "chan"}])
    monkeypatch.setattr(cleaner, "fetch_all_messages", lambda **_: iter([]))

    def fake_delete_messages_older_than(**kwargs):
        return [], {
            "message_count": 0,
            "deleted_count": 1,
            "preserved_deletable_count": 2,
            "reactions_removed_count": 3,
            "preserved_reactions_count": 4,
        }, 0.0

    monkeypatch.setattr(cleaner, "delete_messages_older_than", lambda **kwargs: fake_delete_messages_older_than(**kwargs))
    total = cleaner.clean_messages(
        dry_run=False,
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
        fetch_since=None,
        max_messages=10,
        delete_reactions=True,
    )
    assert total == 1


def test_clean_messages_buffered_non_dry_run_logs_combined_pre_execution_line(monkeypatch, caplog):
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me")
    now = datetime.now(timezone.utc)
    messages = [make_message("1", "me", now - timedelta(days=20))]

    monkeypatch.setattr(cleaner, "get_all_channels", lambda: [{"id": "c1", "type": 0, "name": "chan"}])
    monkeypatch.setattr(cleaner, "fetch_all_messages", lambda **_: iter(messages))

    with caplog.at_level("INFO"):
        total = cleaner.clean_messages(
            dry_run=False,
            fetch_sleep_time_range=(0, 0),
            delete_sleep_time_range=(1, 1),
            fetch_since=None,
            max_messages=10,
            buffer_channel_messages=True,
            delete_reactions=False,
        )

    assert total == 1
    assert "  - Buffered messages=1, scan time=" in caplog.text
    assert "est. execute time=00:00:01" in caplog.text
    assert "Planned " not in caplog.text


def test_clean_messages_logs_channel_and_total_elapsed(monkeypatch, caplog):
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me")
    monkeypatch.setattr(cleaner, "get_all_channels", lambda: [{"id": "c1", "type": 0, "name": "chan"}])
    monkeypatch.setattr(cleaner, "_prepare_channel_messages", lambda **kwargs: (iter(()), None))
    monkeypatch.setattr(
        cleaner,
        "delete_messages_older_than",
        lambda **kwargs: (
            [],
            {
                "message_count": 0,
                "deleted_count": 0,
                "preserved_deletable_count": 0,
                "reactions_removed_count": 0,
                "preserved_reactions_count": 0,
            },
            0.0,
        ),
    )

    monotonic_values = iter([10.0, 12.0, 16.0, 19.0])
    monkeypatch.setattr("delete_me_discord.cleaner.time.monotonic", lambda: next(monotonic_values))

    with caplog.at_level("INFO"):
        total = cleaner.clean_messages(
            dry_run=False,
            fetch_sleep_time_range=(0, 0),
            delete_sleep_time_range=(0, 0),
            fetch_since=None,
            max_messages=10,
            delete_reactions=False,
        )

    assert total == 0
    assert "  - Deleted messages=0, preserved messages=0, total time=00:00:04" in caplog.text
    assert "Total time=00:00:09." in caplog.text


def test_clean_messages_lazy_dry_run_logs_estimates(monkeypatch, caplog):
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me")
    now = datetime.now(timezone.utc)
    messages = [
        make_message("1", "me", now - timedelta(days=20)),
        make_message(
            "2",
            "other",
            now - timedelta(days=21),
            deletable=False,
            reactions=[{"me": True, "emoji": {"name": "x"}}],
        ),
    ]

    monkeypatch.setattr(cleaner, "get_all_channels", lambda: [{"id": "c1", "type": 0, "name": "chan"}])
    monkeypatch.setattr(cleaner, "fetch_all_messages", lambda **_: iter(messages))
    monotonic_values = iter([10.0, 11.0, 12.0, 13.0, 15.0, 16.0])
    monkeypatch.setattr("delete_me_discord.cleaner.time.monotonic", lambda: next(monotonic_values))

    with caplog.at_level("INFO"):
        total = cleaner.clean_messages(
            dry_run=True,
            fetch_sleep_time_range=(0, 0),
            delete_sleep_time_range=(1, 1),
            fetch_since=None,
            max_messages=10,
            buffer_channel_messages=False,
            delete_reactions=True,
        )

    assert total == 1
    assert "  - Would delete messages=1, preserve messages=0, delete reactions=1, preserve reactions=0, scan time=00:00:04, est. execute time=00:00:02, est. total time=00:00:06" in caplog.text
    assert "Summary: Would delete messages=1, preserve messages=0, delete reactions=1, preserve reactions=0, scan time=00:00:06, est. execute time=00:00:02, est. total time=00:00:08" in caplog.text


def test_clean_messages_buffered_dry_run_folds_buffered_count_into_summary(monkeypatch, caplog):
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me")
    now = datetime.now(timezone.utc)
    messages = [
        make_message("1", "me", now - timedelta(days=20)),
        make_message("2", "me", now - timedelta(days=21)),
    ]

    monkeypatch.setattr(cleaner, "get_all_channels", lambda: [{"id": "c1", "type": 0, "name": "chan"}])
    monkeypatch.setattr(cleaner, "fetch_all_messages", lambda **_: iter(messages))

    with caplog.at_level("INFO"):
        total = cleaner.clean_messages(
            dry_run=True,
            fetch_sleep_time_range=(0, 0),
            delete_sleep_time_range=(1, 1),
            fetch_since=None,
            max_messages=10,
            buffer_channel_messages=True,
            delete_reactions=False,
        )

    assert total == 2
    assert "buffered messages=2" in caplog.text
    assert "  - Buffered messages=" not in caplog.text


def test_delete_reactions_skips_non_owner():
    class RecordingAPI:
        def __init__(self):
            self.called = False

        def delete_message(self, channel_id, message_id):
            return True

        def delete_own_reaction(self, channel_id, message_id, emoji):
            self.called = True
            return True

    cleaner = MessageCleaner(api=RecordingAPI(), user_id="me")
    message = make_message("1", "other", datetime.now(timezone.utc), deletable=False, reactions=[{"me": False, "emoji": {"name": "x"}}])
    facts = cleaner._build_message_facts(message=message, delete_reactions=True)
    decision = cleaner._build_message_decision(facts=facts, in_preserve_window=False)
    assert decision.actions == ()


def test_build_channel_plan_creates_message_and_reaction_actions():
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me", preserve_n=0, preserve_n_mode="mine")
    now = datetime.now(timezone.utc)
    messages = [
        make_message("1", "me", now - timedelta(days=20)),
        make_message(
            "2",
            "other",
            now - timedelta(days=21),
            deletable=False,
            reactions=[
                {"me": True, "emoji": {"name": "x"}},
                {"me": True, "emoji": {"name": "y"}},
            ],
        ),
    ]

    plan = cleaner._build_channel_plan(
        messages=messages,
        cutoff_time=now,
        delete_reactions=True,
    )

    assert plan.buffered_message_count == 2
    assert plan.action_count == 3
