from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Tuple, TypedDict

from .type_enums import ReactionType


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


class ActionKind(Enum):
    """Executable operations derived from message decisions."""

    DELETE_MESSAGE = "delete_message"
    DELETE_REACTION = "delete_reaction"


class DeleteOutcome(Enum):
    """Observed result of one idempotent delete request."""

    DELETED = "deleted"
    ABSENT = "absent"
    FAILED = "failed"

    @property
    def desired_state_reached(self) -> bool:
        return self in {DeleteOutcome.DELETED, DeleteOutcome.ABSENT}


@dataclass(frozen=True, slots=True)
class OwnedReaction:
    """One reaction variant owned by the authenticated user."""

    emoji: DiscordEmoji
    reaction_type: ReactionType


@dataclass(frozen=True, slots=True)
class ForeignReactionImpact:
    """Foreign reaction instances affected by deleting a parent artifact."""

    normal: int = 0
    burst: int = 0
    complete: bool = True

    @property
    def total(self) -> int:
        return self.normal + self.burst

    def combined_with(self, other: "ForeignReactionImpact") -> "ForeignReactionImpact":
        return ForeignReactionImpact(
            normal=self.normal + other.normal,
            burst=self.burst + other.burst,
            complete=self.complete and other.complete,
        )


@dataclass(frozen=True, slots=True)
class MessageFacts:
    """Facts derived from one message before preserve/delete policy is applied."""

    message: DiscordMessage
    message_time: datetime
    is_author: bool
    is_deletable: bool
    my_reactions: Tuple[OwnedReaction, ...] = field(default_factory=tuple)
    foreign_reaction_impact: ForeignReactionImpact = field(
        default_factory=ForeignReactionImpact
    )


@dataclass(frozen=True, slots=True)
class PlannedAction:
    """One executable operation in the cleaner pipeline."""

    kind: ActionKind
    channel_id: str
    message_id: str
    message_time: datetime
    emoji: Optional[DiscordEmoji] = None
    reaction_type: ReactionType = ReactionType.NORMAL


@dataclass(frozen=True, slots=True)
class MessageDecision:
    """Policy result for one message, including preserve flags and actions."""

    facts: MessageFacts
    preserve_message: bool
    preserve_reactions: bool
    actions: Tuple[PlannedAction, ...] = field(default_factory=tuple)

    @property
    def preserve_reaction_count(self) -> int:
        return len(self.facts.my_reactions) if self.preserve_reactions else 0

    @property
    def planned_action_count(self) -> int:
        return len(self.actions)


@dataclass(frozen=True, slots=True)
class ChannelPlan:
    """Buffered per-channel plan consisting of message decisions and actions."""

    decisions: Tuple[MessageDecision, ...]

    @property
    def buffered_message_count(self) -> int:
        return len(self.decisions)

    @property
    def actions(self) -> Tuple[PlannedAction, ...]:
        return tuple(action for decision in self.decisions for action in decision.actions)

    @property
    def action_count(self) -> int:
        return sum(decision.planned_action_count for decision in self.decisions)
