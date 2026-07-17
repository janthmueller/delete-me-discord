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


def test_exact_thread_include_overrides_thread_type_filter_without_search():
    api = ScopeAPI()
    preflight = preflight_scope_ids(api, [THREAD_A], [])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=preflight.include_ids,
        scope_seed=preflight.seed,
        scope_filter=ScopeFilter.from_names(
            ["PublicThread"],
            exact_included_channel_ids=[THREAD_A],
        ),
    )

    assert [channel["id"] for channel in cleaner.iter_channels()] == [THREAD_A]
    assert api.thread_search_calls == []


def test_exact_thread_include_uses_preflight_payload_without_search():
    api = ScopeAPI()
    preflight = preflight_scope_ids(api, [THREAD_A], [])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=preflight.include_ids,
        scope_seed=preflight.seed,
        scope_filter=ScopeFilter.from_names(
            exclude_threads=True,
            exact_included_channel_ids=[THREAD_A],
        ),
    )

    contexts = list(cleaner.iter_channel_contexts())

    assert [context.channel["id"] for context in contexts] == [THREAD_A]
    assert contexts[0].parent["id"] == TEXT_A
    assert api.thread_search_calls == []


def test_lazy_thread_context_preserves_guild_and_parent_permissions():
    api = ScopeAPI()
    api.guild_channels[GUILD_A][1]["permission_overwrites"] = [{
        "id": GUILD_A,
        "type": 0,
        "allow": "0",
        "deny": "0",
    }]
    preflight = preflight_scope_ids(api, [THREAD_A], [])
    preflight.seed.guilds[0]["permissions"] = "123"
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=preflight.include_ids,
        scope_seed=preflight.seed,
    )

    contexts = list(cleaner.iter_channel_contexts())

    assert len(contexts) == 1
    assert contexts[0].channel["id"] == THREAD_A
    assert contexts[0].guild["permissions"] == "123"
    assert contexts[0].parent["id"] == TEXT_A
    assert contexts[0].parent["permission_overwrites"][0]["id"] == GUILD_A


def test_exact_forum_parent_scope_processes_posts_but_not_container():
    forum_id = "300000000000000008"
    post_id = "300000000000000009"

    class ForumScopeAPI(ScopeAPI):
        def __init__(self):
            super().__init__()
            self.guild_channels[GUILD_A].append({
                "id": forum_id,
                "type": 15,
                "name": "forum",
                "parent_id": CATEGORY_A,
            })

        def get_channel(self, channel_id):
            if channel_id == forum_id:
                return {
                    "id": forum_id,
                    "type": 15,
                    "name": "forum",
                    "guild_id": GUILD_A,
                    "parent_id": CATEGORY_A,
                }
            return super().get_channel(channel_id)

        def search_channel_threads(self, channel_id, *, include_archived=False):
            if channel_id == forum_id:
                self.thread_search_calls.append(channel_id)
                return [{
                    "id": post_id,
                    "type": 11,
                    "name": "forum-post",
                    "parent_id": forum_id,
                    "owner_id": "me",
                    "thread_metadata": {"archived": True},
                }]
            return super().search_channel_threads(
                channel_id,
                include_archived=include_archived,
            )

    api = ForumScopeAPI()
    preflight = preflight_scope_ids(api, [forum_id], [])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=preflight.include_ids,
        exclude_ids=preflight.exclude_ids,
        scope_seed=preflight.seed,
    )

    included = list(cleaner.iter_channels())

    assert [channel["id"] for channel in included] == [post_id]
    assert included[0]["parent_id"] == forum_id
    assert forum_id in api.thread_search_calls
