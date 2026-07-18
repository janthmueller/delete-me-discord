import pytest

from delete_me_discord.discord.channel_types import (
    FILTERABLE_CHANNEL_TYPE_NAMES,
    GUILD_CLEANUP_CHANNEL_TYPES,
    GUILD_MESSAGE_CHANNEL_TYPES,
    MESSAGE_CHANNEL_TYPES,
    ROOT_MESSAGE_CHANNEL_TYPES,
    THREAD_CHANNEL_TYPES,
    ChannelType,
    channel_type_name,
    is_archived_thread,
    is_message_channel,
)


@pytest.mark.parametrize(
    ("channel_type", "name"),
    [
        (0, "GuildText"),
        (1, "DM"),
        (2, "GuildVoice"),
        (3, "GroupDM"),
        (5, "GuildAnnouncement"),
        (10, "AnnouncementThread"),
        (11, "PublicThread"),
        (12, "PrivateThread"),
        (13, "GuildStageVoice"),
        (15, "GuildForum"),
        (16, "GuildMedia"),
    ],
)
def test_channel_type_names(channel_type, name):
    assert channel_type_name(channel_type) == name


def test_message_channel_sets_are_explicit():
    assert {int(value) for value in ROOT_MESSAGE_CHANNEL_TYPES} == {1, 3}
    assert {int(value) for value in GUILD_MESSAGE_CHANNEL_TYPES} == {0, 2, 5, 13}
    assert {int(value) for value in THREAD_CHANNEL_TYPES} == {10, 11, 12}
    assert {int(value) for value in GUILD_CLEANUP_CHANNEL_TYPES} == {0, 2, 5, 10, 11, 12, 13}
    assert {int(value) for value in MESSAGE_CHANNEL_TYPES} == {0, 1, 2, 3, 5, 10, 11, 12, 13}
    assert FILTERABLE_CHANNEL_TYPE_NAMES == (
        "GuildText",
        "GuildAnnouncement",
        "GuildVoice",
        "GuildStageVoice",
        "AnnouncementThread",
        "PublicThread",
        "PrivateThread",
        "DM",
        "GroupDM",
    )


@pytest.mark.parametrize("channel_type", [4, 14, 15, 16, 99])
def test_container_and_non_message_types_are_not_message_channels(channel_type):
    assert is_message_channel(channel_type) is False


def test_archived_thread_detection_requires_thread_type_and_metadata():
    assert is_archived_thread({"type": ChannelType.PUBLIC_THREAD, "thread_metadata": {"archived": True}})
    assert not is_archived_thread({"type": ChannelType.PUBLIC_THREAD, "thread_metadata": {"archived": False}})
    assert not is_archived_thread({"type": ChannelType.GUILD_TEXT, "thread_metadata": {"archived": True}})
