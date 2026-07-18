import pytest

from delete_me_discord.scope import preflight_scope_ids
from delete_me_discord.discord.errors import ResourceUnavailable


GUILD_A = "100000000000000001"
GUILD_B = "100000000000000002"
GUILD_UNKNOWN = "100000000000000099"
DM = "200000000000000001"
CATEGORY = "300000000000000001"
TEXT = "300000000000000002"
FORUM = "300000000000000003"
THREAD = "300000000000000004"
UNSUPPORTED = "300000000000000005"
UNKNOWN = "9999"


class FakeAPI:
    def __init__(self):
        self.guild_calls = 0
        self.root_calls = 0
        self.channel_calls = []
        self.channels = {
            CATEGORY: {"id": CATEGORY, "type": 4, "guild_id": GUILD_A},
            TEXT: {
                "id": TEXT,
                "type": 0,
                "guild_id": GUILD_A,
                "parent_id": CATEGORY,
            },
            FORUM: {
                "id": FORUM,
                "type": 15,
                "guild_id": GUILD_A,
                "parent_id": CATEGORY,
            },
            THREAD: {
                "id": THREAD,
                "type": 11,
                "guild_id": GUILD_A,
                "parent_id": TEXT,
                "thread_metadata": {"archived": False},
            },
            UNSUPPORTED: {
                "id": UNSUPPORTED,
                "type": 14,
                "guild_id": GUILD_A,
            },
        }

    def get_guilds(self):
        self.guild_calls += 1
        return [
            {"id": GUILD_A, "name": "A"},
            {"id": GUILD_B, "name": "B"},
        ]

    def get_root_channels(self):
        self.root_calls += 1
        return [{"id": DM, "type": 1, "name": "DM"}]

    def get_channel(self, channel_id):
        self.channel_calls.append(channel_id)
        if channel_id not in self.channels:
            raise ResourceUnavailable("not found")
        return self.channels[channel_id]


def test_preflight_classifies_exact_ids_and_builds_safe_guild_allowlist():
    api = FakeAPI()

    result = preflight_scope_ids(
        api,
        [GUILD_A, CATEGORY, THREAD, DM],
        [FORUM],
    )

    assert result.include_ids == (GUILD_A, CATEGORY, THREAD, DM)
    assert result.exclude_ids == (FORUM,)
    assert result.nodes_by_id[GUILD_A].kind == "guild"
    assert result.nodes_by_id[DM].kind == "private-channel"
    assert result.nodes_by_id[CATEGORY].kind == "category"
    assert result.nodes_by_id[FORUM].kind == "thread-parent"
    assert result.nodes_by_id[THREAD].kind == "thread"
    assert result.seed.guild_ids == frozenset({GUILD_A})
    assert api.channel_calls == [CATEGORY, THREAD, FORUM]
    assert api.guild_calls == 1
    assert api.root_calls == 1


def test_preflight_excludes_only_must_leave_all_guilds_traversable():
    result = preflight_scope_ids(FakeAPI(), [], [CATEGORY])

    assert result.seed.guild_ids is None


def test_preflight_dm_only_include_skips_every_guild():
    result = preflight_scope_ids(FakeAPI(), [DM], [])

    assert result.seed.guild_ids == frozenset()


def test_preflight_deduplicates_ids_before_direct_lookup():
    api = FakeAPI()

    result = preflight_scope_ids(api, [CATEGORY, CATEGORY], [])

    assert result.include_ids == (CATEGORY,)
    assert api.channel_calls == [CATEGORY]


@pytest.mark.parametrize("value", ["channel", "12.3", "", "١٢٣"])
def test_preflight_rejects_non_decimal_ids_before_network(value):
    api = FakeAPI()

    with pytest.raises(ValueError, match="must contain only ASCII decimal digits"):
        preflight_scope_ids(api, [value], [])

    assert api.guild_calls == 0
    assert api.root_calls == 0


def test_preflight_rejects_overlap_before_network():
    api = FakeAPI()

    with pytest.raises(ValueError, match="must be disjoint"):
        preflight_scope_ids(api, [CATEGORY], [CATEGORY])

    assert api.guild_calls == 0
    assert api.root_calls == 0


def test_preflight_treats_short_numeric_value_as_unknown_exact_id():
    api = FakeAPI()

    with pytest.raises(ValueError, match="Exact Discord IDs are required"):
        preflight_scope_ids(api, [UNKNOWN], [])

    assert api.channel_calls == [UNKNOWN]


def test_preflight_rejects_unsupported_channel_type():
    with pytest.raises(ValueError, match="unsupported channel type 14"):
        preflight_scope_ids(FakeAPI(), [UNSUPPORTED], [])


def test_preflight_rejects_channel_from_inaccessible_guild():
    api = FakeAPI()
    foreign_channel = "300000000000000099"
    api.channels[foreign_channel] = {
        "id": foreign_channel,
        "type": 0,
        "guild_id": GUILD_UNKNOWN,
    }

    with pytest.raises(ValueError, match="guild that is not accessible"):
        preflight_scope_ids(api, [foreign_channel], [])


def test_preflight_rejects_mismatched_channel_response():
    api = FakeAPI()
    api.channels[TEXT] = {"id": CATEGORY, "type": 0, "guild_id": GUILD_A}

    with pytest.raises(ValueError, match="mismatched data"):
        preflight_scope_ids(api, [TEXT], [])


@pytest.mark.parametrize(
    ("channel_type", "expected_kind"),
    [
        (0, "message-channel"),
        (2, "message-channel"),
        (5, "message-channel"),
        (13, "message-channel"),
        (4, "category"),
        (15, "thread-parent"),
        (16, "thread-parent"),
        (10, "thread"),
        (11, "thread"),
        (12, "thread"),
    ],
)
def test_preflight_classifies_every_supported_guild_scope_type(
    channel_type,
    expected_kind,
):
    api = FakeAPI()
    channel_id = "400000000000000001"
    api.channels[channel_id] = {
        "id": channel_id,
        "type": channel_type,
        "guild_id": GUILD_A,
    }

    result = preflight_scope_ids(api, [channel_id], [])

    assert result.nodes_by_id[channel_id].kind == expected_kind
