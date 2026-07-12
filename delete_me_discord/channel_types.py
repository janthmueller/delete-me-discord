from enum import IntEnum
from typing import Any, Mapping


OWNED_THREAD_DELETE_MODES = ("none", "self-only", "all")


class ChannelType(IntEnum):
    GUILD_TEXT = 0
    DM = 1
    GUILD_VOICE = 2
    GROUP_DM = 3
    GUILD_CATEGORY = 4
    GUILD_ANNOUNCEMENT = 5
    ANNOUNCEMENT_THREAD = 10
    PUBLIC_THREAD = 11
    PRIVATE_THREAD = 12
    GUILD_STAGE_VOICE = 13
    GUILD_DIRECTORY = 14
    GUILD_FORUM = 15
    GUILD_MEDIA = 16


CHANNEL_TYPE_NAMES: dict[int, str] = {
    ChannelType.GUILD_TEXT: "GuildText",
    ChannelType.DM: "DM",
    ChannelType.GUILD_VOICE: "GuildVoice",
    ChannelType.GROUP_DM: "GroupDM",
    ChannelType.GUILD_CATEGORY: "GuildCategory",
    ChannelType.GUILD_ANNOUNCEMENT: "GuildAnnouncement",
    ChannelType.ANNOUNCEMENT_THREAD: "AnnouncementThread",
    ChannelType.PUBLIC_THREAD: "PublicThread",
    ChannelType.PRIVATE_THREAD: "PrivateThread",
    ChannelType.GUILD_STAGE_VOICE: "GuildStageVoice",
    ChannelType.GUILD_DIRECTORY: "GuildDirectory",
    ChannelType.GUILD_FORUM: "GuildForum",
    ChannelType.GUILD_MEDIA: "GuildMedia",
}

_CHANNEL_TYPE_DISPLAY_ORDER = {
    channel_type: index
    for index, channel_type in enumerate((
        ChannelType.GUILD_TEXT,
        ChannelType.GUILD_ANNOUNCEMENT,
        ChannelType.GUILD_VOICE,
        ChannelType.GUILD_STAGE_VOICE,
        ChannelType.GUILD_FORUM,
        ChannelType.GUILD_MEDIA,
        ChannelType.ANNOUNCEMENT_THREAD,
        ChannelType.PUBLIC_THREAD,
        ChannelType.PRIVATE_THREAD,
        ChannelType.DM,
        ChannelType.GROUP_DM,
    ))
}

ROOT_MESSAGE_CHANNEL_TYPES = frozenset({
    ChannelType.DM,
    ChannelType.GROUP_DM,
})

GUILD_MESSAGE_CHANNEL_TYPES = frozenset({
    ChannelType.GUILD_TEXT,
    ChannelType.GUILD_VOICE,
    ChannelType.GUILD_ANNOUNCEMENT,
    ChannelType.GUILD_STAGE_VOICE,
})

THREAD_CHANNEL_TYPES = frozenset({
    ChannelType.ANNOUNCEMENT_THREAD,
    ChannelType.PUBLIC_THREAD,
    ChannelType.PRIVATE_THREAD,
})

GUILD_CLEANUP_CHANNEL_TYPES = frozenset({
    *GUILD_MESSAGE_CHANNEL_TYPES,
    *THREAD_CHANNEL_TYPES,
})

MESSAGE_CHANNEL_TYPES = frozenset({
    *ROOT_MESSAGE_CHANNEL_TYPES,
    *GUILD_CLEANUP_CHANNEL_TYPES,
})

FILTERABLE_CHANNEL_TYPE_NAMES = tuple(
    CHANNEL_TYPE_NAMES[channel_type]
    for channel_type in _CHANNEL_TYPE_DISPLAY_ORDER
    if channel_type in MESSAGE_CHANNEL_TYPES
)

THREAD_CHANNEL_TYPE_NAMES = tuple(
    CHANNEL_TYPE_NAMES[channel_type]
    for channel_type in _CHANNEL_TYPE_DISPLAY_ORDER
    if channel_type in THREAD_CHANNEL_TYPES
)

FILTERABLE_CHANNEL_TYPES_BY_NAME = {
    CHANNEL_TYPE_NAMES[channel_type]: channel_type
    for channel_type in MESSAGE_CHANNEL_TYPES
}

THREAD_PARENT_CHANNEL_TYPES = frozenset({
    ChannelType.GUILD_TEXT,
    ChannelType.GUILD_ANNOUNCEMENT,
    ChannelType.GUILD_FORUM,
    ChannelType.GUILD_MEDIA,
})

THREAD_CONTAINER_CHANNEL_TYPES = frozenset({
    ChannelType.GUILD_FORUM,
    ChannelType.GUILD_MEDIA,
})


def channel_type_name(value: Any) -> str:
    return CHANNEL_TYPE_NAMES.get(value, f"Type {value}")


def channel_type_sort_order(value: Any) -> int:
    return _CHANNEL_TYPE_DISPLAY_ORDER.get(value, len(_CHANNEL_TYPE_DISPLAY_ORDER))


def is_message_channel(value: Any) -> bool:
    return value in MESSAGE_CHANNEL_TYPES


def is_thread_channel(value: Any) -> bool:
    return value in THREAD_CHANNEL_TYPES


def is_archived_thread(channel: Mapping[str, Any]) -> bool:
    if not is_thread_channel(channel.get("type")):
        return False
    metadata = channel.get("thread_metadata")
    return isinstance(metadata, Mapping) and metadata.get("archived") is True
