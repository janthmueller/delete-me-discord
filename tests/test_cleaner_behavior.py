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
from delete_me_discord.models import ActionKind, PlannedAction
from delete_me_discord.privacy import RedactionConfig, set_redaction_config
from delete_me_discord.scope_filter import ScopeFilter
from delete_me_discord.scope_inventory import ScopeInventory
from delete_me_discord.type_enums import ReactionType
from delete_me_discord.utils import DETAIL_LEVEL, PROGRESS_LEVEL


class DummyType:
    def __init__(self, deletable: bool):
        self.deletable = deletable


class DummyAPI:
    def delete_message(self, channel_id, message_id):
        return True

    def delete_own_reaction(
        self,
        channel_id,
        message_id,
        emoji,
        reaction_type=ReactionType.NORMAL,
    ):
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

        def delete_own_reaction(
            self,
            channel_id,
            message_id,
            emoji,
            reaction_type=ReactionType.NORMAL,
        ):
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


def test_delete_messages_streams_each_decision_before_fetching_the_next_message():
    events = []

    class RecordingAPI:
        def delete_message(self, channel_id, message_id):
            events.append(f"delete:{message_id}")
            return True

    cleaner = MessageCleaner(api=RecordingAPI(), user_id="me")
    now = datetime.now(timezone.utc)

    def messages():
        events.append("yield:1")
        yield make_message("1", "me", now - timedelta(days=10))
        assert events[-1] == "delete:1"
        events.append("yield:2")
        yield make_message("2", "me", now - timedelta(days=11))

    _, stats, _ = cleaner.delete_messages_older_than(
        messages=messages(),
        cutoff_time=now,
        delete_sleep_time_range=(0, 0),
    )

    assert stats["deleted_count"] == 2
    assert events == ["yield:1", "delete:1", "yield:2", "delete:2"]


def test_delete_messages_reaction_removal_non_dry_run():
    class RecordingAPI:
        def __init__(self):
            self.reactions = []

        def delete_message(self, channel_id, message_id):
            return True

        def delete_own_reaction(
            self,
            channel_id,
            message_id,
            emoji,
            reaction_type=ReactionType.NORMAL,
        ):
            self.reactions.append(
                (channel_id, message_id, emoji.get("name"), reaction_type)
            )
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
    assert api.reactions == [("c1", "1", "x", ReactionType.NORMAL)]


def test_delete_messages_removes_normal_and_super_reaction_variants():
    class RecordingAPI:
        def __init__(self):
            self.reactions = []

        def delete_own_reaction(
            self,
            channel_id,
            message_id,
            emoji,
            reaction_type=ReactionType.NORMAL,
        ):
            self.reactions.append((emoji["name"], reaction_type))
            return True

    api = RecordingAPI()
    cleaner = MessageCleaner(api=api, user_id="me")
    now = datetime.now(timezone.utc)
    messages = [
        make_message(
            "1",
            "other",
            now - timedelta(days=10),
            deletable=False,
            reactions=[
                {
                    "me": True,
                    "me_burst": True,
                    "emoji": {"name": "sparkles"},
                }
            ],
        )
    ]

    _, stats, _ = cleaner.delete_messages_older_than(
        messages=iter(messages),
        cutoff_time=now,
        delete_sleep_time_range=(0, 0),
        dry_run=False,
        delete_reactions=True,
    )

    assert stats["reactions_removed_count"] == 2
    assert api.reactions == [
        ("sparkles", ReactionType.NORMAL),
        ("sparkles", ReactionType.BURST),
    ]


def test_super_reaction_is_discovered_without_normal_reaction_ownership():
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me")
    message = make_message(
        "1",
        "other",
        datetime.now(timezone.utc),
        deletable=False,
        reactions=[
            {
                "me": False,
                "me_burst": True,
                "emoji": {"name": "sparkles"},
            }
        ],
    )

    facts = cleaner._build_message_facts(message, delete_reactions=True)

    assert len(facts.my_reactions) == 1
    assert facts.my_reactions[0].reaction_type == ReactionType.BURST


def test_foreign_reaction_impact_is_exactly_derived_by_type():
    impact = MessageCleaner._foreign_reaction_impact(
        [
            {
                "count": 4,
                "count_details": {"normal": 3, "burst": 1},
                "me": True,
                "me_burst": False,
                "emoji": {"name": "one"},
            },
            {
                "count": 3,
                "count_details": {"normal": 0, "burst": 3},
                "me": False,
                "me_burst": True,
                "emoji": {"name": "two"},
            },
        ]
    )

    assert impact.normal == 2
    assert impact.burst == 3
    assert impact.total == 5
    assert impact.complete is True


@pytest.mark.parametrize(
    "reaction",
    [
        {"count": 1, "me": False, "emoji": {"name": "missing-details"}},
        {
            "count": 1,
            "count_details": {"normal": 1, "burst": 0},
            "me": False,
            "emoji": {"name": "missing-burst-ownership"},
        },
        {
            "count_details": {"normal": 1, "burst": 0},
            "me": False,
            "me_burst": False,
            "emoji": {"name": "missing-total"},
        },
        {
            "count": 3,
            "count_details": {"normal": 1, "burst": 1},
            "me": False,
            "me_burst": False,
            "emoji": {"name": "inconsistent-total"},
        },
        {
            "count": 0,
            "count_details": {"normal": 0, "burst": 0},
            "me": True,
            "me_burst": False,
            "emoji": {"name": "impossible-owner"},
        },
    ],
)
def test_foreign_reaction_impact_is_unknown_for_incomplete_payload(reaction):
    impact = MessageCleaner._foreign_reaction_impact([reaction])

    assert impact.complete is False


def test_message_dry_run_reports_foreign_reactions_removed_by_parent_delete():
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me")
    now = datetime.now(timezone.utc)
    messages = [
        make_message(
            "1",
            "me",
            now - timedelta(days=10),
            reactions=[
                {
                    "count": 5,
                    "count_details": {"normal": 3, "burst": 2},
                    "me": True,
                    "me_burst": True,
                    "emoji": {"name": "wave"},
                }
            ],
        )
    ]

    _, stats, _ = cleaner.delete_messages_older_than(
        messages=messages,
        cutoff_time=now,
        delete_sleep_time_range=(0, 0),
        dry_run=True,
    )

    assert stats["foreign_reactions_normal_count"] == 2
    assert stats["foreign_reactions_burst_count"] == 1
    assert stats["foreign_reactions_unknown_count"] == 0
    assert "foreign reactions affected 2 normal / 1 super" in (
        cleaner._format_channel_summary(
            stats=stats,
            delete_reactions=False,
            dry_run=True,
        )
    )


def test_message_dry_run_reports_unknown_foreign_reactions_without_counts():
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me")
    now = datetime.now(timezone.utc)
    messages = [
        make_message(
            "1",
            "me",
            now - timedelta(days=10),
            reactions=[{"me": False, "emoji": {"name": "wave"}}],
        )
    ]

    _, stats, _ = cleaner.delete_messages_older_than(
        messages=messages,
        cutoff_time=now,
        delete_sleep_time_range=(0, 0),
        dry_run=True,
    )

    assert stats["foreign_reactions_unknown_count"] == 1
    assert "foreign reactions affected unknown" in cleaner._format_channel_summary(
        stats=stats,
        delete_reactions=False,
        dry_run=True,
    )


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
            self.save_calls = 0

        def get_ids(self, channel_id):
            return ["90"]

        def set_ids(self, channel_id, message_ids):
            self.set_calls.append((channel_id, list(message_ids)))

        def save(self):
            self.save_calls += 1

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

    monkeypatch.setattr(cleaner, "iter_channels", lambda: iter([{"id": "c1", "type": 0, "name": "chan"}]))
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
    assert cache.save_calls == 1


def test_unknown_message_type_is_not_deleted():
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me")
    message = make_message("1", "me", datetime.now(timezone.utc))
    message["type"] = 999

    facts = cleaner._build_message_facts(message=message, delete_reactions=False)

    assert facts.is_author is True
    assert facts.is_deletable is False


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

    monkeypatch.setattr(cleaner, "iter_channels", lambda: iter([{"id": "c1", "type": 0, "name": "chan"}]))
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


def test_clean_messages_buffered_mode_fetches_all_messages_before_deleting(monkeypatch):
    events = []

    class RecordingAPI:
        def delete_message(self, channel_id, message_id):
            events.append(f"delete:{message_id}")
            return True

    cleaner = MessageCleaner(api=RecordingAPI(), user_id="me")
    now = datetime.now(timezone.utc)

    def messages():
        events.append("yield:1")
        yield make_message("1", "me", now - timedelta(days=10))
        events.append("yield:2")
        yield make_message("2", "me", now - timedelta(days=11))

    monkeypatch.setattr(
        cleaner,
        "iter_channels",
        lambda: iter([{"id": "c1", "type": 0, "name": "chan"}]),
    )
    monkeypatch.setattr(cleaner, "fetch_all_messages", lambda **_: messages())

    cleaner.clean_messages(
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
        buffer_channel_messages=True,
    )

    assert events == ["yield:1", "yield:2", "delete:1", "delete:2"]


def test_delete_messages_handles_delete_failure():
    class FailingAPI:
        def delete_message(self, channel_id, message_id):
            return False

        def delete_own_reaction(
            self,
            channel_id,
            message_id,
            emoji,
            reaction_type=ReactionType.NORMAL,
        ):
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

        def delete_own_reaction(
            self,
            channel_id,
            message_id,
            emoji,
            reaction_type=ReactionType.NORMAL,
        ):
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


def test_delete_reaction_logs_redact_emoji_name_and_ids(caplog):
    cleaner = MessageCleaner(
        api=DummyAPI(),
        user_id="me",
        preserve_last=timedelta(weeks=2),
        preserve_n=0,
        preserve_n_mode="mine",
    )
    action = PlannedAction(
        kind=ActionKind.DELETE_REACTION,
        channel_id="123456789012345678",
        message_id="123456789012345679",
        message_time=datetime.now(timezone.utc),
        emoji={"name": "sample_emoji"},
    )

    set_redaction_config(RedactionConfig(enabled=True, prefix=0, suffix=4))
    try:
        with caplog.at_level("DEBUG"):
            executed = cleaner._execute_action(
                action=action,
                dry_run=True,
            )
    finally:
        set_redaction_config(RedactionConfig())

    assert executed is True
    assert "Would delete reaction from message ***5679." in caplog.text
    assert "Reaction: ***" in caplog.text
    assert "sample_emoji" not in caplog.text
    assert "123456789012345678" not in caplog.text
    assert "123456789012345679" not in caplog.text



def test_grouped_reaction_deletes_log_once_per_message(caplog):
    cleaner = MessageCleaner(
        api=DummyAPI(),
        user_id="me",
        preserve_last=timedelta(0),
        preserve_n=0,
        preserve_n_mode="mine",
    )
    now = datetime.now(timezone.utc)
    message = make_message(
        "123456789012345679",
        "other-user",
        now - timedelta(days=30),
        reactions=[
            {"emoji": {"name": "heart_fire"}, "me": True},
            {"emoji": {"name": "heart"}, "me": True},
        ],
    )

    with caplog.at_level(DETAIL_LEVEL):
        preserved_ids, stats, _ = cleaner.delete_messages_older_than(
            messages=[message],
            cutoff_time=now - timedelta(days=1),
            delete_sleep_time_range=(0, 0),
            dry_run=True,
            delete_reactions=True,
        )

    assert preserved_ids == []
    assert stats["deleted_count"] == 0
    assert stats["reactions_removed_count"] == 2
    assert "Would delete 2 reactions from message 123456789012345679." in caplog.text
    assert "Reactions: heart_fire, heart" in caplog.text
    assert caplog.text.count("Would delete") == 1


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

        def get_guild_channels(self, guild_id):
            assert guild_id == "g1"
            return [
                {"id": "c1", "type": 0, "name": "chan"},
                {"id": "c2", "type": 2, "name": "voice"},
                {"id": "c3", "type": 5, "name": "announcements"},
                {"id": "c4", "type": 13, "name": "stage"},
                {"id": "forum", "type": 15, "name": "forum"},
                {"id": "unknown", "type": 99, "name": "unknown"},
            ]

        def search_channel_threads(self, channel_id, *, include_archived=False):
            return []

        def get_root_channels(self):
            return [
                {"id": "dm1", "type": 1, "recipients": [{"username": "Amy"}]},
                {"id": "dm2", "type": 99, "recipients": [{"username": "Skip"}]},
            ]

    cleaner = MessageCleaner(api=Api(), user_id="me")
    channels = cleaner.get_all_channels()
    assert [c["id"] for c in channels] == ["dm1", "c1", "c2", "c3", "c4"]


def test_get_all_channels_discovers_all_threads_by_default():
    class Api:
        def __init__(self):
            self.search_calls = []

        def get_guilds(self):
            return [{"id": "g1"}]

        def get_root_channels(self):
            return []

        def get_guild_channels(self, guild_id):
            return [{"id": "text", "type": 0, "name": "text"}]

        def search_channel_threads(self, channel_id, *, include_archived=False):
            self.search_calls.append((channel_id, include_archived))
            return [{
                "id": "thread",
                "type": 11,
                "name": "thread",
                "parent_id": channel_id,
                "thread_metadata": {"archived": True},
            }]

    api = Api()
    cleaner = MessageCleaner(api=api, user_id="me")

    assert [channel["id"] for channel in cleaner.get_all_channels()] == [
        "text",
        "thread",
    ]
    assert api.search_calls == [("text", True)]


def test_clean_messages_discovers_and_processes_one_thread_parent_at_a_time():
    events = []

    class Api:
        def get_guilds(self):
            events.append("get:guilds")
            return [{"id": "g1"}, {"id": "g2"}]

        def get_root_channels(self):
            events.append("get:root-channels")
            return [{"id": "dm", "type": 1, "name": "DM"}]

        def get_guild_channels(self, guild_id):
            events.append(f"get:guild-channels:{guild_id}")
            if guild_id == "g1":
                return [
                    {"id": "category", "type": 4, "name": "Category"},
                    {
                        "id": "text",
                        "type": 0,
                        "name": "Text",
                        "parent_id": "category",
                    },
                    {
                        "id": "forum",
                        "type": 15,
                        "name": "Forum",
                        "parent_id": "category",
                    },
                ]
            return [{"id": "voice", "type": 2, "name": "Voice"}]

        def search_channel_threads(self, channel_id, *, include_archived=False):
            assert include_archived is True
            events.append(f"search:{channel_id}")
            thread_id = "thread" if channel_id == "text" else "post"
            return [{
                "id": thread_id,
                "type": 11,
                "name": thread_id,
                "parent_id": channel_id,
                "thread_metadata": {"archived": False},
            }]

        def fetch_messages(self, channel_id, **kwargs):
            events.append(f"process:{channel_id}")
            return iter(())

    cleaner = MessageCleaner(api=Api(), user_id="me")

    cleaner.clean_messages(
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert events == [
        "get:guilds",
        "get:root-channels",
        "process:dm",
        "get:guild-channels:g1",
        "search:text",
        "process:text",
        "process:thread",
        "search:forum",
        "process:post",
        "get:guild-channels:g2",
        "process:voice",
    ]


def test_get_all_channels_exclude_threads_shortcut_avoids_search():
    class Api:
        def get_guilds(self):
            return [{"id": "g1"}]

        def get_root_channels(self):
            return []

        def get_guild_channels(self, guild_id):
            return [{"id": "text", "type": 0, "name": "text"}]

        def search_channel_threads(self, channel_id, *, include_archived=False):
            raise AssertionError("Thread search should be skipped.")

    cleaner = MessageCleaner(
        api=Api(),
        user_id="me",
        scope_filter=ScopeFilter.from_names(exclude_threads=True),
    )

    assert [channel["id"] for channel in cleaner.get_all_channels()] == ["text"]


def test_get_all_channels_reuses_scope_inventory_without_fetching_api():
    class Api:
        def get_guilds(self):
            raise AssertionError("guilds should not be fetched when scope inventory is provided")

        def get_guild_channels_multiple(self, guild_ids):
            raise AssertionError("guild channels should not be fetched when scope inventory is provided")

        def get_root_channels(self):
            raise AssertionError("root channels should not be fetched when scope inventory is provided")

    inventory = ScopeInventory(
        guilds=[{"id": "g1", "name": "Guild"}],
        root_channels=[{"id": "dm1", "type": 1, "recipients": [{"username": "Amy"}]}],
        guild_channels_by_guild={
            "g1": [
                {"id": "c1", "type": 0, "name": "chan"},
                {"id": "cat1", "type": 4, "name": "category"},
            ],
        },
    )
    cleaner = MessageCleaner(api=Api(), user_id="me", scope_inventory=inventory)

    channels = cleaner.get_all_channels()

    assert [channel["id"] for channel in channels] == ["dm1", "c1"]
    assert channels[1]["guild_id"] == "g1"


def test_clean_messages_non_dry_run_summary(monkeypatch):
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me")
    monkeypatch.setattr(cleaner, "iter_channels", lambda: iter([{"id": "c1", "type": 0, "name": "chan"}]))
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

    monkeypatch.setattr(cleaner, "iter_channels", lambda: iter([{"id": "c1", "type": 0, "name": "chan"}]))
    monkeypatch.setattr(cleaner, "fetch_all_messages", lambda **_: iter(messages))

    with caplog.at_level(PROGRESS_LEVEL):
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
    assert "Buffered messages=1, scan time=" in caplog.text
    assert "est. execute time=00:00:00" in caplog.text
    assert "Planned " not in caplog.text


def test_clean_messages_logs_channel_and_total_elapsed(monkeypatch, caplog):
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me")
    monkeypatch.setattr(cleaner, "iter_channels", lambda: iter([{"id": "c1", "type": 0, "name": "chan"}]))
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

    with caplog.at_level(PROGRESS_LEVEL):
        total = cleaner.clean_messages(
            dry_run=False,
            fetch_sleep_time_range=(0, 0),
            delete_sleep_time_range=(0, 0),
            fetch_since=None,
            max_messages=10,
            delete_reactions=False,
    )

    assert total == 0
    assert "Summary: messages 0 deleted / 0 kept" in caplog.text
    assert "total time=00:00:04" in caplog.text
    assert "Total time=00:00:09." not in caplog.text


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

    monkeypatch.setattr(cleaner, "iter_channels", lambda: iter([{"id": "c1", "type": 0, "name": "chan"}]))
    monkeypatch.setattr(cleaner, "fetch_all_messages", lambda **_: iter(messages))
    monotonic_values = iter([10.0, 11.0, 12.0, 13.0, 15.0, 16.0])
    monkeypatch.setattr("delete_me_discord.cleaner.time.monotonic", lambda: next(monotonic_values))

    with caplog.at_level(PROGRESS_LEVEL):
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
    assert "Summary: messages 1 delete / 0 keep, reactions 1 delete / 0 keep" in caplog.text
    assert "scan time=00:00:04, est. execute time=00:00:01, est. total time=00:00:05" in caplog.text
    assert "Summary: messages 1 delete / 0 keep, reactions 1 delete / 0 keep" in caplog.text
    assert "scan time=00:00:06, est. execute time=00:00:01, est. total time=00:00:07" in caplog.text


def test_clean_messages_buffered_dry_run_folds_buffered_count_into_summary(monkeypatch, caplog):
    cleaner = MessageCleaner(api=DummyAPI(), user_id="me")
    now = datetime.now(timezone.utc)
    messages = [
        make_message("1", "me", now - timedelta(days=20)),
        make_message("2", "me", now - timedelta(days=21)),
    ]

    monkeypatch.setattr(cleaner, "iter_channels", lambda: iter([{"id": "c1", "type": 0, "name": "chan"}]))
    monkeypatch.setattr(cleaner, "fetch_all_messages", lambda **_: iter(messages))

    with caplog.at_level(PROGRESS_LEVEL):
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

        def delete_own_reaction(
            self,
            channel_id,
            message_id,
            emoji,
            reaction_type=ReactionType.NORMAL,
        ):
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
