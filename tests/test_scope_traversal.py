import pytest

from delete_me_discord.cleaner import MessageCleaner
from delete_me_discord.scope_filter import ScopeFilter
from delete_me_discord.scope_ids import preflight_scope_ids


GUILD_A = "100000000000000001"
GUILD_B = "100000000000000002"
DM = "200000000000000001"
CATEGORY_A = "300000000000000001"
TEXT_A = "300000000000000002"
THREAD_A = "300000000000000003"
CATEGORY_B = "300000000000000004"
TEXT_B = "300000000000000005"
CATEGORY_C = "300000000000000006"
TEXT_C = "300000000000000007"


class ScopeAPI:
    def __init__(self):
        self.guild_calls = 0
        self.root_calls = 0
        self.guild_channel_calls = []
        self.thread_search_calls = []
        self.guild_channels = {
            GUILD_A: [
                {"id": CATEGORY_A, "type": 4, "name": "A"},
                {
                    "id": TEXT_A,
                    "type": 0,
                    "name": "text-a",
                    "parent_id": CATEGORY_A,
                },
                {"id": CATEGORY_B, "type": 4, "name": "B"},
                {
                    "id": TEXT_B,
                    "type": 0,
                    "name": "text-b",
                    "parent_id": CATEGORY_B,
                },
            ],
            GUILD_B: [
                {"id": CATEGORY_C, "type": 4, "name": "C"},
                {
                    "id": TEXT_C,
                    "type": 0,
                    "name": "text-c",
                    "parent_id": CATEGORY_C,
                },
            ],
        }

    def get_guilds(self):
        self.guild_calls += 1
        return [{"id": GUILD_A, "name": "A"}, {"id": GUILD_B, "name": "B"}]

    def get_root_channels(self):
        self.root_calls += 1
        return [{"id": DM, "type": 1, "name": "DM"}]

    def get_channel(self, channel_id):
        for guild_id, channels in self.guild_channels.items():
            for channel in channels:
                if channel["id"] == channel_id:
                    return {**channel, "guild_id": guild_id}
        if channel_id == THREAD_A:
            return {
                "id": THREAD_A,
                "type": 11,
                "name": "thread-a",
                "guild_id": GUILD_A,
                "parent_id": TEXT_A,
                "thread_metadata": {"archived": False},
            }
        raise AssertionError(f"Unexpected direct lookup: {channel_id}")

    def get_guild_channels(self, guild_id):
        self.guild_channel_calls.append(guild_id)
        return self.guild_channels[guild_id]

    def search_channel_threads(self, channel_id, *, include_archived=False):
        self.thread_search_calls.append(channel_id)
        if channel_id == TEXT_A:
            return [{
                "id": THREAD_A,
                "type": 11,
                "name": "thread-a",
                "parent_id": TEXT_A,
                "thread_metadata": {"archived": False},
            }]
        return []


@pytest.mark.parametrize(
    ("include_ids", "exclude_ids", "expected", "expected_guild_calls"),
    [
        ([CATEGORY_A], [GUILD_A], [TEXT_A, THREAD_A], [GUILD_A]),
        ([GUILD_A], [CATEGORY_A], [TEXT_B], [GUILD_A]),
        ([TEXT_A], [CATEGORY_A], [TEXT_A, THREAD_A], [GUILD_A]),
        ([CATEGORY_A], [TEXT_A], [], [GUILD_A]),
        ([THREAD_A], [TEXT_A], [THREAD_A], [GUILD_A]),
        ([TEXT_A], [THREAD_A], [TEXT_A], [GUILD_A]),
        ([GUILD_A, THREAD_A], [CATEGORY_A], [THREAD_A, TEXT_B], [GUILD_A]),
        ([DM], [], [DM], []),
        ([], [GUILD_A], [DM, TEXT_C], [GUILD_A, GUILD_B]),
    ],
)
def test_lazy_traversal_preserves_scope_rule_semantics(
    include_ids,
    exclude_ids,
    expected,
    expected_guild_calls,
):
    api = ScopeAPI()
    preflight = preflight_scope_ids(api, include_ids, exclude_ids)
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=preflight.include_ids,
        exclude_ids=preflight.exclude_ids,
        scope_seed=preflight.seed,
    )

    included = [channel["id"] for channel in cleaner.iter_channels()]

    assert included == expected
    assert api.guild_channel_calls == expected_guild_calls
    assert api.guild_calls == 1
    assert api.root_calls == 1


def test_channel_type_filter_still_overrides_exact_thread_include():
    api = ScopeAPI()
    preflight = preflight_scope_ids(api, [THREAD_A], [])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=preflight.include_ids,
        scope_seed=preflight.seed,
        scope_filter=ScopeFilter.from_names(["PublicThread"]),
    )

    assert list(cleaner.iter_channels()) == []
