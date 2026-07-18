from itertools import product

import pytest

from delete_me_discord.scope import ScopeRules


CHANNELS = [
    {"id": "channel-a", "parent_id": "category-a", "guild_id": "guild-a"},
    {
        "id": "thread-a",
        "parent_id": "channel-a",
        "category_id": "category-a",
        "guild_id": "guild-a",
    },
    {"id": "channel-b", "parent_id": "category-b", "guild_id": "guild-a"},
    {"id": "channel-c", "parent_id": "category-c", "guild_id": "guild-b"},
    {"id": "dm"},
]


def _legacy_includes(channel, include_ids, exclude_ids):
    """Frozen characterization of the nearest-target policy before this refactor."""
    scope_chain = (
        channel.get("id"),
        channel.get("parent_id"),
        channel.get("category_id"),
        channel.get("guild_id"),
    )
    for scope_id in scope_chain:
        if scope_id is None:
            continue
        if scope_id in exclude_ids:
            return False
        if scope_id in include_ids:
            return True
    return not include_ids


@pytest.mark.parametrize(
    ("include_ids", "exclude_ids", "expected"),
    [
        (set(), set(), {"channel-a", "thread-a", "channel-b", "channel-c", "dm"}),
        (set(), {"guild-a"}, {"channel-c", "dm"}),
        ({"guild-a"}, set(), {"channel-a", "thread-a", "channel-b"}),
        ({"category-a"}, {"guild-a"}, {"channel-a", "thread-a"}),
        ({"guild-a"}, {"category-a"}, {"channel-b"}),
        ({"channel-a"}, {"category-a"}, {"channel-a", "thread-a"}),
        ({"category-a"}, {"channel-a"}, set()),
        ({"thread-a"}, {"channel-a"}, {"thread-a"}),
        ({"channel-a"}, {"thread-a"}, {"channel-a"}),
        ({"dm"}, set(), {"dm"}),
        (set(), {"dm"}, {"channel-a", "thread-a", "channel-b", "channel-c"}),
    ],
)
def test_scope_rules_preserve_nearest_target_semantics(
    include_ids,
    exclude_ids,
    expected,
):
    rules = ScopeRules.from_values(include_ids, exclude_ids)

    included = {channel["id"] for channel in CHANNELS if rules.includes(channel)}

    assert included == expected


def test_scope_rules_reject_exact_include_exclude_overlap():
    with pytest.raises(ValueError, match="must be disjoint"):
        ScopeRules.from_values(["same"], ["same"])


def test_scope_rules_exhaustively_match_pre_refactor_policy():
    for channel in CHANNELS:
        relevant_ids = tuple(
            dict.fromkeys(
                str(scope_id)
                for scope_id in (
                    channel.get("id"),
                    channel.get("parent_id"),
                    channel.get("category_id"),
                    channel.get("guild_id"),
                    "unrelated",
                )
                if scope_id is not None
            )
        )
        for assignments in product(("unset", "include", "exclude"), repeat=len(relevant_ids)):
            include_ids = {
                scope_id
                for scope_id, assignment in zip(relevant_ids, assignments)
                if assignment == "include"
            }
            exclude_ids = {
                scope_id
                for scope_id, assignment in zip(relevant_ids, assignments)
                if assignment == "exclude"
            }

            actual = ScopeRules.from_values(include_ids, exclude_ids).includes(channel)
            expected = _legacy_includes(channel, include_ids, exclude_ids)

            assert actual is expected, (
                f"channel={channel!r}, include_ids={include_ids!r}, "
                f"exclude_ids={exclude_ids!r}"
            )
