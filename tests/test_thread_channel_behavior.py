from datetime import timedelta

from delete_me_discord.cleaner import MessageCleaner
from delete_me_discord.discovery import collect_channels_from_inventory
from delete_me_discord.scope_filter import ScopeFilter
from delete_me_discord.scope_inventory import ScopeInventory
from delete_me_discord.scope_selectors import discover_scope_targets
from delete_me_discord.type_enums import MessageType
from delete_me_discord.utils import should_include_channel


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


def test_scope_targets_include_thread_containers_threads_and_direct_message_channels():
    by_id = {target.id: target for target in discover_scope_targets(make_inventory())}

    assert by_id["cat"].kind == "Category"
    assert by_id["voice"].kind == "GuildVoice"
    assert by_id["news"].kind == "GuildAnnouncement"
    assert by_id["stage"].kind == "GuildStageVoice"
    assert by_id["forum"].kind == "GuildForum"
    assert by_id["active-thread"].kind == "PublicThread"
    assert by_id["archived-post"].kind == "PublicThread"


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
        "voice",
        "news",
        "stage",
        "active-thread",
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

        def delete_own_reaction(self, channel_id, message_id, emoji):
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
