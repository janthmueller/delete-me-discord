import logging

import pytest

from delete_me_discord.cleanup import MessageCleaner
from delete_me_discord.discovery import collect_channels_from_inventory
from delete_me_discord.scope import ScopeInventory, resolve_scope


GUILD_A = "100000000000000001"
GUILD_B = "100000000000000002"
DM = "200000000000000001"
CATEGORY_A = "300000000000000001"
TEXT_A = "300000000000000002"
VOICE_A = "300000000000000003"
FORUM_A = "300000000000000004"
ACTIVE_THREAD = "300000000000000005"
ARCHIVED_THREAD = "300000000000000006"
ACTIVE_POST = "300000000000000007"
ARCHIVED_POST = "300000000000000008"
TEXT_B = "300000000000000009"


class ScopeParityAPI:
    def __init__(self):
        self.logger = logging.getLogger("scope-parity-test")
        self.thread_search_calls: list[tuple[str, bool]] = []
        self.guild_channels = {
            GUILD_A: [
                {
                    "id": CATEGORY_A,
                    "type": 4,
                    "name": "Category",
                    "guild_id": GUILD_A,
                },
                {
                    "id": TEXT_A,
                    "type": 0,
                    "name": "Text",
                    "guild_id": GUILD_A,
                    "parent_id": CATEGORY_A,
                },
                {
                    "id": VOICE_A,
                    "type": 2,
                    "name": "Voice",
                    "guild_id": GUILD_A,
                    "parent_id": CATEGORY_A,
                },
                {
                    "id": FORUM_A,
                    "type": 15,
                    "name": "Forum",
                    "guild_id": GUILD_A,
                    "parent_id": CATEGORY_A,
                },
            ],
            GUILD_B: [
                {
                    "id": TEXT_B,
                    "type": 0,
                    "name": "Other",
                    "guild_id": GUILD_B,
                },
            ],
        }
        self.threads = {
            ACTIVE_THREAD: self._thread(
                ACTIVE_THREAD,
                TEXT_A,
                archived=False,
            ),
            ARCHIVED_THREAD: self._thread(
                ARCHIVED_THREAD,
                TEXT_A,
                archived=True,
            ),
            ACTIVE_POST: self._thread(
                ACTIVE_POST,
                FORUM_A,
                archived=False,
            ),
            ARCHIVED_POST: self._thread(
                ARCHIVED_POST,
                FORUM_A,
                archived=True,
            ),
        }

    @staticmethod
    def _thread(thread_id, parent_id, *, archived):
        return {
            "id": thread_id,
            "type": 11,
            "name": thread_id,
            "guild_id": GUILD_A,
            "parent_id": parent_id,
            "thread_metadata": {"archived": archived},
        }

    def get_guilds(self):
        return [
            {"id": GUILD_A, "name": "Guild A"},
            {"id": GUILD_B, "name": "Guild B"},
        ]

    def get_root_channels(self):
        return [{"id": DM, "type": 1, "name": "DM"}]

    def get_channel(self, channel_id):
        for channels in self.guild_channels.values():
            for channel in channels:
                if channel["id"] == channel_id:
                    return dict(channel)
        if channel_id in self.threads:
            return dict(self.threads[channel_id])
        raise AssertionError(f"Unexpected exact channel lookup: {channel_id}")

    def get_guild_channels(self, guild_id):
        channels = [dict(channel) for channel in self.guild_channels[guild_id]]
        for channel in channels:
            channel.pop("guild_id", None)
        return channels

    def search_channel_threads(self, channel_id, *, include_archived=False):
        self.thread_search_calls.append((channel_id, include_archived))
        return [
            dict(thread)
            for thread in self.threads.values()
            if thread["parent_id"] == channel_id
            and (include_archived or not thread["thread_metadata"]["archived"])
        ]


def _cleanup_target_ids(api, scope_kwargs):
    scope = resolve_scope(api, **scope_kwargs)
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=list(scope.include_ids),
        exclude_ids=list(scope.exclude_ids),
        scope_seed=scope.seed,
        scope_filter=scope.scope_filter,
    )
    return {channel["id"] for channel in cleaner.iter_channels()}


def _listed_cleanup_target_ids(api, scope_kwargs):
    scope = resolve_scope(api, **scope_kwargs)
    inventory = ScopeInventory.fetch(
        api,
        scope_filter=scope.scope_filter,
        seed=scope.seed,
    )
    data = collect_channels_from_inventory(
        inventory,
        set(scope.include_ids),
        set(scope.exclude_ids),
    )
    ids = {
        channel["id"]
        for channel in data["dms"]
        if channel["cleanup_target"]
    }
    ids.update(
        channel["id"]
        for guild in data["guilds"]
        for category in guild["categories"]
        for channel in category["channels"]
        if channel["cleanup_target"]
    )
    return ids


@pytest.mark.parametrize(
    ("scope_kwargs", "expected"),
    [
        (
            {},
            {
                DM,
                TEXT_A,
                VOICE_A,
                ACTIVE_THREAD,
                ARCHIVED_THREAD,
                ACTIVE_POST,
                ARCHIVED_POST,
                TEXT_B,
            },
        ),
        (
            {
                "include_ids": [CATEGORY_A],
                "exclude_ids": [GUILD_A],
            },
            {
                TEXT_A,
                VOICE_A,
                ACTIVE_THREAD,
                ARCHIVED_THREAD,
                ACTIVE_POST,
                ARCHIVED_POST,
            },
        ),
        (
            {
                "include_ids": [GUILD_A],
                "exclude_ids": [CATEGORY_A],
            },
            set(),
        ),
        (
            {
                "include_ids": [ACTIVE_THREAD],
                "exclude_ids": [TEXT_A],
            },
            {ACTIVE_THREAD},
        ),
        (
            {
                "include_ids": [ACTIVE_THREAD],
                "exclude_threads": True,
            },
            {ACTIVE_THREAD},
        ),
        (
            {
                "include_ids": [ARCHIVED_THREAD],
                "included_channel_types": ["GuildText"],
            },
            {ARCHIVED_THREAD},
        ),
        (
            {
                "include_ids": [TEXT_A],
                "excluded_channel_types": ["GuildText"],
            },
            {TEXT_A, ACTIVE_THREAD, ARCHIVED_THREAD},
        ),
        (
            {
                "included_channel_types": ["PublicThread"],
                "excluded_thread_states": ["archived"],
            },
            {ACTIVE_THREAD, ACTIVE_POST},
        ),
        (
            {"included_thread_states": ["archived"]},
            {ARCHIVED_THREAD, ARCHIVED_POST},
        ),
        (
            {
                "include_ids": [FORUM_A],
                "exclude_ids": [GUILD_A],
            },
            {ACTIVE_POST, ARCHIVED_POST},
        ),
        ({"include_ids": [DM]}, {DM}),
        ({"exclude_ids": [GUILD_A]}, {DM, TEXT_B}),
    ],
)
def test_list_and_cleanup_discover_the_same_cleanup_targets(
    scope_kwargs,
    expected,
):
    clean_ids = _cleanup_target_ids(ScopeParityAPI(), scope_kwargs)
    list_ids = _listed_cleanup_target_ids(ScopeParityAPI(), scope_kwargs)

    assert clean_ids == expected
    assert list_ids == expected


def test_exact_thread_scope_does_not_trigger_broad_thread_search():
    api = ScopeParityAPI()

    listed = _listed_cleanup_target_ids(
        api,
        {
            "include_ids": [ACTIVE_THREAD],
            "exclude_threads": True,
        },
    )

    assert listed == {ACTIVE_THREAD}
    assert api.thread_search_calls == []
