from datetime import datetime, timedelta, timezone

from delete_me_discord.cleaner import MessageCleaner
from delete_me_discord.discovery import collect_channels_from_inventory
from delete_me_discord.scope_filter import ScopeFilter
from delete_me_discord.scope_inventory import ScopeInventory
from delete_me_discord.type_enums import MessageType, ReactionType
from delete_me_discord.utils import PROGRESS_LEVEL, should_include_channel


def make_inventory() -> ScopeInventory:
    return ScopeInventory(
        guilds=[{"id": "g1", "name": "Guild"}],
        root_channels=[{"id": "dm", "type": 1, "recipients": [{"username": "Amy"}]}],
        guild_channels_by_guild={
            "g1": [
                {"id": "cat", "type": 4, "name": "Category"},
                {"id": "text", "type": 0, "name": "Text", "parent_id": "cat"},
                {"id": "voice", "type": 2, "name": "Voice", "parent_id": "cat"},
                {"id": "news", "type": 5, "name": "News", "parent_id": "cat"},
                {"id": "stage", "type": 13, "name": "Stage", "parent_id": "cat"},
                {"id": "forum", "type": 15, "name": "Forum", "parent_id": "cat"},
            ],
        },
        threads_by_guild={
            "g1": [
                {
                    "id": "active-thread",
                    "type": 11,
                    "name": "Active thread",
                    "guild_id": "g1",
                    "parent_id": "text",
                    "category_id": "cat",
                    "thread_metadata": {"archived": False},
                },
                {
                    "id": "archived-post",
                    "type": 11,
                    "name": "Archived post",
                    "guild_id": "g1",
                    "parent_id": "forum",
                    "category_id": "cat",
                    "thread_metadata": {"archived": True},
                },
            ],
        },
    )


def make_owned_thread_inventory(
    *,
    owner_id: str = "me",
    message_count: int = 1,
) -> ScopeInventory:
    inventory = make_inventory()
    thread = dict(inventory.threads_by_guild["g1"][0])
    thread["owner_id"] = owner_id
    thread["message_count"] = message_count
    inventory.threads_by_guild["g1"] = [thread]
    return inventory


def make_thread_message(
    message_id: str,
    author_id: str,
    *,
    reactions=None,
):
    return {
        "message_id": message_id,
        "timestamp": "2020-01-01T00:00:00+00:00",
        "channel_id": "active-thread",
        "type": MessageType.DEFAULT,
        "author_id": author_id,
        "author_username": author_id,
        "content": "old",
        "reactions": reactions or [],
    }


class ThreadCleanupAPI:
    def __init__(
        self,
        messages,
        *,
        complete: bool = True,
        delete_thread_result: bool = True,
    ):
        self.messages = list(messages)
        self.complete = complete
        self.delete_thread_result = delete_thread_result
        self.fetch_calls = []
        self.deleted_threads = []
        self.deleted_messages = []
        self.deleted_reactions = []

    def fetch_messages(self, channel_id, **kwargs):
        self.fetch_calls.append((channel_id, kwargs))
        yield from self.messages

    def get_last_fetch_summary(self, channel_id):
        return {
            "fetched_count": len(self.messages),
            "stop_reason": "exhausted channel history",
            "wait_count": 0,
            "waited_seconds": 0.0,
            "complete": self.complete,
        }

    def delete_thread(self, thread_id):
        self.deleted_threads.append(thread_id)
        return self.delete_thread_result

    def delete_message(self, channel_id, message_id):
        self.deleted_messages.append((channel_id, message_id))
        return True

    def delete_own_reaction(
        self,
        channel_id,
        message_id,
        emoji,
        reaction_type=ReactionType.NORMAL,
    ):
        self.deleted_reactions.append(
            (channel_id, message_id, emoji, reaction_type)
        )
        return True


def test_discovery_renders_threads_in_their_category_with_parent_context():
    data = collect_channels_from_inventory(make_inventory(), include_set=set(), exclude_set=set())

    categories = data["guilds"][0]["categories"]
    assert [category["id"] for category in categories] == ["cat"]
    entries = {entry["id"]: entry for entry in categories[0]["channels"]}
    assert entries["forum"]["type"] == "GuildForum"
    assert entries["active-thread"] == {
        "id": "active-thread",
        "name": "Active thread",
        "type": "PublicThread",
        "parent_id": "text",
        "parent_name": "Text",
        "archived": False,
    }
    assert entries["archived-post"]["parent_name"] == "Forum"
    assert entries["archived-post"]["archived"] is True


def test_scope_filtering_uses_channel_parent_category_and_guild_specificity():
    thread = {
        "id": "thread",
        "guild_id": "guild",
        "parent_id": "forum",
        "category_id": "category",
    }

    assert should_include_channel(thread, {"category"}, {"guild"})
    assert not should_include_channel(thread, {"category"}, {"forum"})
    assert should_include_channel(thread, {"forum"}, {"category"})
    assert should_include_channel(thread, {"thread"}, {"forum", "category", "guild"})
    assert not should_include_channel(thread, {"forum", "category", "guild"}, {"thread"})


def test_channel_type_exclusion_wins_over_explicit_thread_id():
    thread = {
        "id": "thread",
        "type": 12,
        "parent_id": "forum",
        "category_id": "category",
        "guild_id": "guild",
        "thread_metadata": {"archived": False},
    }
    scope_filter = ScopeFilter.from_names(["PrivateThread"])

    assert not should_include_channel(
        thread,
        {"thread"},
        set(),
        scope_filter=scope_filter,
    )


def test_cleaner_processes_direct_and_thread_channels_but_not_containers():
    cleaner = MessageCleaner(api=object(), user_id="me", scope_inventory=make_inventory())

    assert [channel["id"] for channel in cleaner.get_all_channels()] == [
        "dm",
        "text",
        "active-thread",
        "voice",
        "news",
        "stage",
        "archived-post",
    ]


def test_cleaner_applies_the_same_channel_type_filter_as_discovery():
    cleaner = MessageCleaner(
        api=object(),
        user_id="me",
        scope_inventory=make_inventory(),
        scope_filter=ScopeFilter.from_names(["DM", "GuildVoice", "PublicThread"]),
    )

    assert [channel["id"] for channel in cleaner.get_all_channels()] == [
        "text",
        "news",
        "stage",
    ]


def test_archived_thread_deletes_own_messages_but_skips_reactions(caplog):
    caplog.set_level("INFO")

    class API:
        def __init__(self):
            self.deleted_messages = []
            self.deleted_reactions = []

        def fetch_messages(self, channel_id, **kwargs):
            assert channel_id == "archived-post"
            yield {
                "message_id": "mine",
                "timestamp": "2020-01-01T00:00:00+00:00",
                "channel_id": channel_id,
                "type": MessageType.DEFAULT,
                "author_id": "me",
                "author_username": "Me",
                "content": "old",
                "reactions": [],
            }
            yield {
                "message_id": "theirs",
                "timestamp": "2020-01-01T00:00:00+00:00",
                "channel_id": channel_id,
                "type": MessageType.DEFAULT,
                "author_id": "other",
                "author_username": "Other",
                "content": "old",
                "reactions": [{"me": True, "emoji": {"name": "wave"}}],
            }

        def delete_message(self, channel_id, message_id):
            self.deleted_messages.append((channel_id, message_id))
            return True

        def delete_own_reaction(
            self,
            channel_id,
            message_id,
            emoji,
            reaction_type=ReactionType.NORMAL,
        ):
            self.deleted_reactions.append((channel_id, message_id, emoji))
            return True

        def get_last_fetch_summary(self, channel_id):
            return None

    api = API()
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        preserve_last=timedelta(0),
        scope_inventory=make_inventory(),
    )

    deleted = cleaner.clean_messages(
        delete_reactions=True,
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_messages == [("archived-post", "mine")]
    assert api.deleted_reactions == []
    assert "Skipping reaction cleanup in archived thread" in caplog.text


def test_owned_thread_deletion_is_disabled_by_default():
    api = ThreadCleanupAPI([make_thread_message("mine", "me")])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(),
    )

    deleted = cleaner.clean_messages(
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_threads == []
    assert api.deleted_messages == [("active-thread", "mine")]


def test_owned_thread_all_mode_deletes_without_scanning_messages():
    api = ThreadCleanupAPI([make_thread_message("theirs", "other")])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(),
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="all",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 0
    assert api.deleted_threads == ["active-thread"]
    assert api.fetch_calls == []
    assert api.deleted_messages == []


def test_owned_thread_all_mode_skips_thread_created_by_another_user():
    api = ThreadCleanupAPI([make_thread_message("mine", "me")])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(owner_id="other"),
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="all",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_threads == []
    assert api.deleted_messages == [("active-thread", "mine")]


def test_owned_thread_all_mode_falls_back_when_owner_is_missing():
    api = ThreadCleanupAPI([make_thread_message("mine", "me")])
    inventory = make_owned_thread_inventory()
    inventory.threads_by_guild["g1"][0].pop("owner_id")
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=inventory,
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="all",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_threads == []
    assert api.deleted_messages == [("active-thread", "mine")]


def test_owned_thread_all_mode_dry_run_only_plans_deletion(caplog):
    caplog.set_level(PROGRESS_LEVEL)
    api = ThreadCleanupAPI(
        [
            make_thread_message(
                "mine",
                "me",
                reactions=[
                    {
                        "count": 4,
                        "count_details": {"normal": 3, "burst": 1},
                        "me": True,
                        "me_burst": False,
                        "emoji": {"name": "one"},
                    }
                ],
            ),
            make_thread_message(
                "theirs",
                "other",
                reactions=[
                    {
                        "count": 3,
                        "count_details": {"normal": 1, "burst": 2},
                        "me": False,
                        "me_burst": True,
                        "emoji": {"name": "two"},
                    }
                ],
            ),
        ]
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(message_count=2),
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="all",
        dry_run=True,
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 0
    assert api.deleted_threads == []
    assert len(api.fetch_calls) == 1
    assert (
        "Impact at scan time for PublicThread Active thread (ID: active-thread): "
        "messages 1 yours / 1 other-or-unknown; foreign reactions 3 normal / 2 super."
        in caplog.text
    )
    assert "owned threads 1 delete" in caplog.text
    assert "foreign messages affected 1" in caplog.text
    assert "foreign reactions affected 3 normal / 2 super" in caplog.text


def test_owned_thread_all_mode_falls_back_when_discord_rejects_deletion():
    api = ThreadCleanupAPI(
        [make_thread_message("mine", "me")],
        delete_thread_result=False,
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(),
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="all",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_threads == ["active-thread"]
    assert len(api.fetch_calls) == 1
    assert api.deleted_messages == [("active-thread", "mine")]


def test_owned_thread_self_only_deletes_after_complete_all_own_scan():
    api = ThreadCleanupAPI(
        [
            make_thread_message(
                "mine",
                "me",
                reactions=[{"me": False, "emoji": {"name": "wave"}}],
            )
        ]
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(),
    )
    fetch_since = datetime(2025, 1, 1, tzinfo=timezone.utc)

    deleted = cleaner.clean_messages(
        delete_owned_threads="self-only",
        fetch_since=fetch_since,
        max_messages=1,
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 0
    assert api.deleted_threads == ["active-thread"]
    assert len(api.fetch_calls) == 1
    _, scan_options = api.fetch_calls[0]
    assert scan_options["fetch_since"] is None
    assert scan_options["max_messages"] == float("inf")


def test_owned_thread_self_only_falls_back_for_foreign_message():
    api = ThreadCleanupAPI(
        [
            make_thread_message("mine", "me"),
            make_thread_message("theirs", "other"),
        ]
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(message_count=2),
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="self-only",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_threads == []
    assert len(api.fetch_calls) == 1
    assert api.deleted_messages == [("active-thread", "mine")]


def test_owned_thread_self_only_falls_back_when_scan_is_not_proven_complete(
    caplog,
):
    caplog.set_level(PROGRESS_LEVEL)
    api = ThreadCleanupAPI(
        [
            make_thread_message(
                "mine",
                "me",
                reactions=[
                    {
                        "count": 2,
                        "count_details": {"normal": 2, "burst": 0},
                        "me": True,
                        "me_burst": False,
                        "emoji": {"name": "wave"},
                    }
                ],
            )
        ],
        complete=False,
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(),
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="self-only",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_threads == []
    assert api.deleted_messages == [("active-thread", "mine")]
    assert (
        "messages unknown (incomplete thread scan); foreign reactions unknown"
        in caplog.text
    )


def test_owned_thread_self_only_falls_back_when_thread_count_is_inconsistent():
    api = ThreadCleanupAPI([make_thread_message("mine", "me")])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(message_count=2),
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="self-only",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_threads == []
    assert api.deleted_messages == [("active-thread", "mine")]
