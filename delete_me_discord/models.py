from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Tuple, TypedDict


class DiscordEmoji(TypedDict, total=False):
    """Subset of Discord's emoji payload used for reaction deletion."""

    id: Optional[str]
    name: Optional[str]


class DiscordReaction(TypedDict, total=False):
    """Subset of Discord's reaction payload used by the cleaner."""

    emoji: DiscordEmoji
    me: bool


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
    recipients: list[DiscordRecipient]


class DiscordMessage(TypedDict):
    """Normalized message shape returned by DiscordAPI.fetch_messages."""

    message_id: str
    timestamp: str
    channel_id: str
    type: Any
    author_id: Optional[str]
    reactions: list[DiscordReaction]


class ActionKind(Enum):
    """Executable operations derived from message decisions."""

    DELETE_MESSAGE = "delete_message"
    DELETE_REACTION = "delete_reaction"


@dataclass(frozen=True, slots=True)
class MessageFacts:
    """Facts derived from one message before preserve/delete policy is applied."""

    message: DiscordMessage
    message_time: datetime
    is_author: bool
    is_deletable: bool
    my_reactions: Tuple[DiscordReaction, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PlannedAction:
    """One executable operation in the cleaner pipeline."""

    kind: ActionKind
    channel_id: str
    message_id: str
    message_time: datetime
    emoji: Optional[DiscordEmoji] = None


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
