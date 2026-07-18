"""Cleanup planning and execution building blocks."""

from .executor import ChannelExecutor
from .models import (
    ActionKind,
    ChannelCleanupStats,
    ChannelExecutionResult,
    ChannelPlan,
    CleanupRunStats,
    CleanupRunOptions,
    ForeignReactionImpact,
    MessageDecision,
    MessageFacts,
    OwnedReaction,
    OwnedThreadDeletionOutcome,
    PlannedAction,
    ThreadDeletionImpact,
)
from .planner import CleanupPlanner, CleanupPolicy
from .preserve_cache import DEFAULT_PRESERVE_CACHE_PATH, PreserveCache
from .reporting import CleanupReporter
from .service import MessageCleaner
from .thread_deletion import OwnedThreadDeletionCoordinator
from .threads import (
    ARCHIVED_THREAD_CLEANUP_MODES,
    ADMINISTRATOR_PERMISSION,
    MANAGE_THREADS_PERMISSION,
    ArchivedThreadAssessment,
    ArchivedThreadCoordinator,
    ThreadRestorationJournal,
    ThreadRestoreOutcome,
    effective_channel_permissions,
)

__all__ = [
    "ActionKind",
    "ADMINISTRATOR_PERMISSION",
    "ARCHIVED_THREAD_CLEANUP_MODES",
    "ArchivedThreadAssessment",
    "ArchivedThreadCoordinator",
    "ChannelCleanupStats",
    "ChannelExecutionResult",
    "ChannelPlan",
    "ChannelExecutor",
    "CleanupPlanner",
    "CleanupPolicy",
    "CleanupReporter",
    "CleanupRunStats",
    "CleanupRunOptions",
    "DEFAULT_PRESERVE_CACHE_PATH",
    "ForeignReactionImpact",
    "MessageDecision",
    "MessageFacts",
    "MessageCleaner",
    "MANAGE_THREADS_PERMISSION",
    "OwnedReaction",
    "OwnedThreadDeletionCoordinator",
    "OwnedThreadDeletionOutcome",
    "PlannedAction",
    "PreserveCache",
    "ThreadDeletionImpact",
    "ThreadRestorationJournal",
    "ThreadRestoreOutcome",
    "effective_channel_permissions",
]
