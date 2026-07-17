import pytest

from delete_me_discord.scope_selectors import parse_scope_selectors


def test_scope_selectors_classify_ids_types_group_and_states():
    selectors = parse_scope_selectors(
        [
            "100000000000000001",
            "GuildText",
            "threads",
            "archived",
        ],
        [
            "200000000000000001",
            "GuildVoice",
            "active",
        ],
    )

    assert selectors.include_ids == ("100000000000000001",)
    assert selectors.exclude_ids == ("200000000000000001",)
    assert selectors.included_channel_types == ("GuildText",)
    assert selectors.excluded_channel_types == ("GuildVoice",)
    assert selectors.included_thread_states == ("archived",)
    assert selectors.excluded_thread_states == ("active",)
    assert selectors.include_threads is True
    assert selectors.exclude_threads is False


def test_scope_selectors_deduplicate_values_without_reordering():
    selectors = parse_scope_selectors(
        ["GuildText", "GuildText", "100000000000000001"],
        [],
    )

    assert selectors.included_channel_types == ("GuildText",)
    assert selectors.include_ids == ("100000000000000001",)


@pytest.mark.parametrize(
    "value",
    ["voice", "Guildtext", "123-not-an-id", ""],
)
def test_scope_selectors_reject_unknown_values(value):
    with pytest.raises(ValueError, match="Unknown --include selector"):
        parse_scope_selectors([value], [])
