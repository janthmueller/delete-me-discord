import logging

from delete_me_discord.scope_inventory import ScopeInventory
from delete_me_discord.scope_filter import ScopeFilter
from delete_me_discord.utils import DIAGNOSTIC_LEVEL, ResourceUnavailable


class ThreadAPI:
    def __init__(self):
        self.logger = logging.getLogger("thread-inventory-test")
        self.search_calls = []

    def get_guilds(self):
        return [{"id": "g1", "name": "Guild"}]

    def get_root_channels(self):
        return []

    def get_guild_channels(self, guild_id):
        assert guild_id == "g1"
        return [
            {"id": "cat", "type": 4, "name": "Category"},
            {"id": "text", "type": 0, "name": "Text", "parent_id": "cat"},
            {"id": "news", "type": 5, "name": "News", "parent_id": "cat"},
            {"id": "forum", "type": 15, "name": "Forum", "parent_id": "cat"},
            {"id": "media", "type": 16, "name": "Media", "parent_id": "cat"},
            {"id": "voice", "type": 2, "name": "Voice", "parent_id": "cat"},
        ]

    def search_channel_threads(self, channel_id, *, include_archived=False):
        self.search_calls.append((channel_id, include_archived))
        threads = []
        if channel_id == "text":
            threads.append({
                "id": "active",
                "type": 11,
                "name": "Active",
                "parent_id": "text",
                "owner_id": "me",
                "message_count": 3,
                "thread_metadata": {"archived": False},
            })
        if include_archived:
            thread_type = 10 if channel_id == "news" else 11
            threads.append({
                "id": f"public-{channel_id}",
                "type": thread_type,
                "name": f"Public {channel_id}",
                "parent_id": channel_id,
                "thread_metadata": {"archived": True},
            })
            if channel_id == "text":
                threads.append({
                    "id": "private-text",
                    "type": 12,
                    "name": "Private",
                    "parent_id": channel_id,
                    "thread_metadata": {"archived": True},
                })
        return threads


def test_inventory_discovers_all_accessible_threads_by_default():
    api = ThreadAPI()

    inventory = ScopeInventory.fetch(api)

    assert {thread["id"] for thread in inventory.guild_threads("g1")} == {
        "active",
        "public-text",
        "public-news",
        "public-forum",
        "public-media",
        "private-text",
    }
    assert api.search_calls == [
        ("text", True),
        ("news", True),
        ("forum", True),
        ("media", True),
    ]


def test_inventory_skips_thread_search_when_all_thread_types_are_excluded():
    api = ThreadAPI()

    inventory = ScopeInventory.fetch(
        api,
        scope_filter=ScopeFilter.without_threads(),
    )

    assert inventory.guild_threads("g1") == []
    assert inventory.thread_mode == "none"
    assert api.search_calls == []


def test_inventory_can_exclude_archived_threads_and_adds_hierarchy():
    api = ThreadAPI()
    scope_filter = ScopeFilter.from_names(excluded_thread_states=["archived"])

    inventory = ScopeInventory.fetch(api, scope_filter=scope_filter)
    threads = {thread["id"]: thread for thread in inventory.guild_threads("g1")}

    assert api.search_calls == [
        ("text", False),
        ("news", False),
        ("forum", False),
        ("media", False),
    ]
    assert set(threads) == {"active"}
    assert all(thread["guild_id"] == "g1" for thread in threads.values())
    assert all(thread["category_id"] == "cat" for thread in threads.values())
    assert threads["active"]["owner_id"] == "me"
    assert threads["active"]["message_count"] == 3
    assert inventory.thread_mode == "active"
    assert inventory.includes_threads is True
    assert inventory.includes_archived_threads is False


def test_inventory_skips_parents_that_cannot_contain_an_included_thread_type():
    api = ThreadAPI()
    scope_filter = ScopeFilter.from_names(["PublicThread"])

    inventory = ScopeInventory.fetch(api, scope_filter=scope_filter)

    assert api.search_calls == [("text", True), ("news", True)]
    assert {thread["id"] for thread in inventory.guild_threads("g1")} == {
        "private-text",
        "public-news",
    }


def test_inventory_skips_one_unavailable_thread_parent_without_losing_other_threads(caplog):
    caplog.set_level(DIAGNOSTIC_LEVEL)
    class API(ThreadAPI):
        def search_channel_threads(self, channel_id, *, include_archived=False):
            if channel_id == "news":
                raise ResourceUnavailable("forbidden")
            return super().search_channel_threads(
                channel_id,
                include_archived=include_archived,
            )

    inventory = ScopeInventory.fetch(API())

    ids = {thread["id"] for thread in inventory.guild_threads("g1")}
    assert "public-news" not in ids
    assert "public-forum" in ids
    assert "unavailable" in caplog.text
    assert all(record.levelno == DIAGNOSTIC_LEVEL for record in caplog.records)
