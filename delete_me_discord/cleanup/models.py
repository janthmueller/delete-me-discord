from collections.abc import Iterator, Mapping
from dataclasses import Field, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Optional, Tuple

from ..models import DiscordEmoji, DiscordMessage
from ..type_enums import ReactionType


class ActionKind(Enum):
    """Executable operations derived from message decisions."""

    DELETE_MESSAGE = "delete_message"
    DELETE_REACTION = "delete_reaction"


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


class StatsMapping(Mapping[str, int]):
    """Dataclass-backed integer counters with legacy read-only mapping access."""

    __dataclass_fields__: ClassVar[dict[str, Field[Any]]]

    def __getitem__(self, key: str) -> int:
        if key not in self.__dataclass_fields__:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(self.__dataclass_fields__)

    def __len__(self) -> int:
        return len(self.__dataclass_fields__)


@dataclass(slots=True)
class ChannelCleanupStats(StatsMapping):
    """Outcomes and retained artifacts observed while processing one channel."""

    message_count: int = 0
    deleted_count: int = 0
    absent_count: int = 0
    failed_count: int = 0
    preserved_deletable_count: int = 0
    reactions_removed_count: int = 0
    reactions_absent_count: int = 0
    reactions_failed_count: int = 0
    preserved_reactions_count: int = 0
    foreign_reactions_normal_count: int = 0
    foreign_reactions_burst_count: int = 0
    foreign_reactions_unknown_count: int = 0
    actions_not_attempted_count: int = 0
    thread_state_interrupted_count: int = 0

    def add_foreign_reaction_impact(
        self,
        impact: ForeignReactionImpact,
    ) -> None:
        self.foreign_reactions_normal_count += impact.normal
        self.foreign_reactions_burst_count += impact.burst
        self.foreign_reactions_unknown_count += int(not impact.complete)


@dataclass(slots=True)
class CleanupRunStats(StatsMapping):
    """Aggregate outcomes from all channels processed by one cleanup run."""

    deleted_count: int = 0
    absent_count: int = 0
    failed_count: int = 0
    preserved_deletable_count: int = 0
    reactions_removed_count: int = 0
    reactions_absent_count: int = 0
    reactions_failed_count: int = 0
    preserved_reactions_count: int = 0
    threads_deleted_count: int = 0
    threads_absent_count: int = 0
    threads_failed_count: int = 0
    threads_planned_count: int = 0
    foreign_messages_affected_count: int = 0
    foreign_messages_unknown_count: int = 0
    foreign_reactions_normal_count: int = 0
    foreign_reactions_burst_count: int = 0
    foreign_reactions_unknown_count: int = 0
    archived_threads_skipped_count: int = 0
    archived_threads_planned_count: int = 0
    archived_threads_opened_count: int = 0
    archived_threads_open_failed_count: int = 0
    archived_threads_restored_count: int = 0
    archived_threads_absent_count: int = 0
    archived_threads_left_active_count: int = 0
    archived_threads_auto_reopened_count: int = 0
    archived_threads_interrupted_count: int = 0
    archived_thread_actions_not_attempted_count: int = 0

    def add_channel_stats(self, stats: ChannelCleanupStats) -> None:
        self.deleted_count += stats.deleted_count
        self.absent_count += stats.absent_count
        self.failed_count += stats.failed_count
        self.preserved_deletable_count += stats.preserved_deletable_count
        self.reactions_removed_count += stats.reactions_removed_count
        self.reactions_absent_count += stats.reactions_absent_count
        self.reactions_failed_count += stats.reactions_failed_count
        self.preserved_reactions_count += stats.preserved_reactions_count
        self.foreign_reactions_normal_count += (
            stats.foreign_reactions_normal_count
        )
        self.foreign_reactions_burst_count += (
            stats.foreign_reactions_burst_count
        )
        self.foreign_reactions_unknown_count += (
            stats.foreign_reactions_unknown_count
        )
        self.archived_threads_interrupted_count += (
            stats.thread_state_interrupted_count
        )
        self.archived_thread_actions_not_attempted_count += (
            stats.actions_not_attempted_count
        )

    def add_foreign_reaction_impact(
        self,
        impact: ForeignReactionImpact,
    ) -> None:
        self.foreign_reactions_normal_count += impact.normal
        self.foreign_reactions_burst_count += impact.burst
        self.foreign_reactions_unknown_count += int(not impact.complete)

    def merge(self, other: "CleanupRunStats") -> None:
        for name in self:
            setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass(frozen=True, slots=True)
class CleanupRunOptions:
    """Resolved behavior and pacing settings for one cleanup run."""

    cutoff_time: datetime
    dry_run: bool
    fetch_sleep_time_range: tuple[float, float]
    delete_sleep_time_range: tuple[float, float]
    fetch_since: Optional[datetime]
    max_messages: int | float
    buffer_channel_messages: bool
    delete_reactions: bool
    delete_owned_threads: str
    archived_thread_cleanup: str


@dataclass(frozen=True, slots=True)
class ChannelExecutionResult:
    """Preservation state, counters, and timing returned by channel execution."""

    preserved_message_ids: tuple[str, ...]
    stats: ChannelCleanupStats
    action_elapsed: float

    def as_legacy_tuple(self) -> tuple[list[str], ChannelCleanupStats, float]:
        return list(self.preserved_message_ids), self.stats, self.action_elapsed
