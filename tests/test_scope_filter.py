import pytest

from delete_me_discord.discord.channel_types import ChannelType
from delete_me_discord.scope import ScopeFilter


def thread(channel_type, *, archived):
    return {
        "id": "thread",
        "type": channel_type,
        "thread_metadata": {"archived": archived},
    }


def test_default_filter_includes_all_thread_types_and_states():
    scope_filter = ScopeFilter()

    assert scope_filter.thread_discovery_mode == "all"
    assert scope_filter.includes_channel(
        thread(ChannelType.ANNOUNCEMENT_THREAD, archived=False)
    )
    assert scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=True)
    )
    assert scope_filter.includes_channel(
        thread(ChannelType.PRIVATE_THREAD, archived=False)
    )


def test_channel_type_exclusions_are_exact():
    scope_filter = ScopeFilter.from_names(["GuildVoice", "PrivateThread"])

    assert not scope_filter.includes_channel({"type": ChannelType.GUILD_VOICE})
    assert scope_filter.includes_channel({"type": ChannelType.GUILD_TEXT})
    assert not scope_filter.includes_channel(
        thread(ChannelType.PRIVATE_THREAD, archived=False)
    )
    assert scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=False)
    )


def test_archived_exclusion_uses_active_only_search():
    scope_filter = ScopeFilter.from_names(excluded_thread_states=["archived"])

    assert scope_filter.thread_discovery_mode == "active"
    assert scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=False)
    )
    assert not scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=True)
    )


def test_active_exclusion_fetches_all_then_filters_active_threads():
    scope_filter = ScopeFilter.from_names(excluded_thread_states=["active"])

    assert scope_filter.thread_discovery_mode == "all"
    assert not scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=False)
    )
    assert scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=True)
    )


def test_excluding_all_thread_types_disables_thread_discovery():
    scope_filter = ScopeFilter.from_names([
        "AnnouncementThread",
        "PublicThread",
        "PrivateThread",
    ])

    assert scope_filter.thread_discovery_mode == "none"
    assert not scope_filter.searches_thread_parent(ChannelType.GUILD_TEXT)
    assert not scope_filter.searches_thread_parent(ChannelType.GUILD_ANNOUNCEMENT)
    assert not scope_filter.searches_thread_parent(ChannelType.GUILD_FORUM)


def test_exclude_threads_shortcut_disables_every_thread_type():
    scope_filter = ScopeFilter.from_names(exclude_threads=True)

    assert scope_filter == ScopeFilter.without_threads()
    assert scope_filter.thread_discovery_mode == "none"


def test_concrete_thread_type_include_overrides_thread_group_exclusion():
    scope_filter = ScopeFilter.from_names(
        exclude_threads=True,
        included_channel_types=["PublicThread"],
    )

    assert scope_filter.thread_discovery_mode == "all"
    assert scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=False)
    )
    assert not scope_filter.includes_channel(
        thread(ChannelType.PRIVATE_THREAD, archived=False)
    )
    assert not scope_filter.includes_channel(
        thread(ChannelType.ANNOUNCEMENT_THREAD, archived=False)
    )


def test_concrete_thread_type_exclusion_overrides_thread_group_include():
    scope_filter = ScopeFilter.from_names(
        excluded_channel_types=["PublicThread"],
        include_threads=True,
    )

    assert not scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=False)
    )
    assert scope_filter.includes_channel(
        thread(ChannelType.PRIVATE_THREAD, archived=False)
    )
    assert scope_filter.includes_channel(
        thread(ChannelType.ANNOUNCEMENT_THREAD, archived=False)
    )


def test_concrete_thread_type_exclusion_wins_at_equal_specificity():
    scope_filter = ScopeFilter.from_names(
        excluded_channel_types=["PublicThread"],
        included_channel_types=["PublicThread"],
    )

    assert scope_filter.thread_discovery_mode == "none"
    assert not scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=False)
    )


def test_thread_state_include_does_not_override_thread_group_exclusion():
    scope_filter = ScopeFilter.from_names(
        exclude_threads=True,
        included_thread_states=["archived"],
    )

    assert scope_filter.thread_discovery_mode == "none"
    assert not scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=True)
    )


def test_parent_searches_are_skipped_when_no_included_type_can_exist_there():
    no_public = ScopeFilter.from_names(["PublicThread"])
    no_announcement = ScopeFilter.from_names(["AnnouncementThread"])

    assert no_public.searches_thread_parent(ChannelType.GUILD_TEXT)
    assert not no_public.searches_thread_parent(ChannelType.GUILD_FORUM)
    assert not no_public.searches_thread_parent(ChannelType.GUILD_MEDIA)
    assert not no_announcement.searches_thread_parent(ChannelType.GUILD_ANNOUNCEMENT)
    assert no_announcement.searches_thread_parent(ChannelType.GUILD_TEXT)


@pytest.mark.parametrize(
    ("types", "states", "match"),
    [
        (["ForumPost"], [], "Unknown excluded channel type"),
        ([], ["locked"], "Unknown excluded thread state"),
    ],
)
def test_filter_rejects_unknown_names(types, states, match):
    with pytest.raises(ValueError, match=match):
        ScopeFilter.from_names(types, states)


def test_positive_channel_type_selection_excludes_other_types_and_threads():
    scope_filter = ScopeFilter.from_names(
        included_channel_types=["GuildText"],
    )

    assert scope_filter.includes_channel({"type": ChannelType.GUILD_TEXT})
    assert not scope_filter.includes_channel({"type": ChannelType.GUILD_VOICE})
    assert not scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=False)
    )
    assert scope_filter.thread_discovery_mode == "none"


def test_positive_thread_state_selection_selects_only_threads_in_that_state():
    scope_filter = ScopeFilter.from_names(
        included_thread_states=["archived"],
    )

    assert not scope_filter.includes_channel({"type": ChannelType.GUILD_TEXT})
    assert not scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=False)
    )
    assert scope_filter.includes_channel(
        thread(ChannelType.PRIVATE_THREAD, archived=True)
    )
    assert scope_filter.thread_discovery_mode == "all"


def test_thread_state_narrows_explicit_thread_type_without_hiding_non_threads():
    scope_filter = ScopeFilter.from_names(
        included_channel_types=["GuildText", "PublicThread"],
        included_thread_states=["archived"],
    )

    assert scope_filter.includes_channel({"type": ChannelType.GUILD_TEXT})
    assert not scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=False)
    )
    assert scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=True)
    )
    assert not scope_filter.includes_channel(
        thread(ChannelType.PRIVATE_THREAD, archived=True)
    )


def test_exact_leaf_include_overrides_broad_type_and_state_exclusions():
    scope_filter = ScopeFilter.from_names(
        excluded_channel_types=["PublicThread"],
        excluded_thread_states=["archived"],
        exact_included_channel_ids=["thread"],
    )

    assert scope_filter.includes_channel(
        thread(ChannelType.PUBLIC_THREAD, archived=True)
    )
