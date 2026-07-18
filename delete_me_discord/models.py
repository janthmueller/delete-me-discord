from enum import Enum
from typing import Any, Optional, TypedDict


class DiscordEmoji(TypedDict, total=False):
    """Subset of Discord's emoji payload used for reaction deletion."""

    id: Optional[str]
    name: Optional[str]


class DiscordReactionCountDetails(TypedDict, total=False):
    """Discord's normal and burst count breakdown for one emoji."""

    normal: int
    burst: int


class DiscordReaction(TypedDict, total=False):
    """Subset of Discord's reaction payload used by the cleaner."""

    emoji: DiscordEmoji
    count: int
    count_details: DiscordReactionCountDetails
    me: bool
    me_burst: bool


class DiscordRecipient(TypedDict, total=False):
    """Recipient info used for DM/group DM channel display names."""

    username: str


class DiscordChannel(TypedDict, total=False):
    """Subset of Discord channel fields used by discovery and cleaning."""

    id: str
    type: int
    name: str
    guild_id: str
    parent_id: str
    category_id: str
    owner_id: str
    message_count: int
    recipients: list[DiscordRecipient]
    thread_metadata: dict[str, Any]
    permission_overwrites: list[dict[str, Any]]


class DiscordMessage(TypedDict):
    """Normalized message shape returned by DiscordAPI.fetch_messages."""

    message_id: str
    timestamp: str
    channel_id: str
    type: Any
    author_id: Optional[str]
    author_username: Optional[str]
    content: Optional[str]
    reactions: list[DiscordReaction]


class DeleteOutcome(Enum):
    """Observed result of one idempotent delete request."""

    DELETED = "deleted"
    ABSENT = "absent"
    THREAD_ARCHIVED = "thread_archived"
    FAILED = "failed"

    @property
    def desired_state_reached(self) -> bool:
        return self in {DeleteOutcome.DELETED, DeleteOutcome.ABSENT}


class UpdateOutcome(Enum):
    """Observed result of one idempotent state update."""

    APPLIED = "applied"
    ABSENT = "absent"
    FAILED = "failed"

    @property
    def desired_state_reached(self) -> bool:
        return self in {UpdateOutcome.APPLIED, UpdateOutcome.ABSENT}
