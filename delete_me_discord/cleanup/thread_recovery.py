"""Pure state models and decisions for archived-thread recovery."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


_AUTO_ARCHIVE_EARLY_TOLERANCE_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class ArchivedThreadActivation:
    """Result of attempting to make one archived thread active."""

    opened: bool
    restore_expected: bool
    journaled: bool = False
    activated_at: float | None = None
    auto_archive_duration_seconds: float | None = None
    locked: bool | None = None


@dataclass(frozen=True, slots=True)
class ActiveThreadBaseline:
    """Latest activity evidence captured before an active thread can archive."""

    archive_timestamp: datetime | None
    last_message_timestamp: datetime | None
    auto_archive_duration_seconds: float | None
    locked: bool | None
    pinned: bool | None


class ArchiveRecoveryReason(Enum):
    """Typed result of evaluating one observed archive transition."""

    LIKELY_AUTOMATIC = "likely-automatic"
    DEADLINE_UNKNOWN = "deadline-unknown"
    DURATION_CHANGED = "duration-changed"
    LOCK_CHANGED = "lock-changed"
    PIN_CHANGED = "pin-changed"
    PINNED = "pinned"
    ACTIVITY_UNKNOWN = "activity-unknown"
    ARCHIVE_BEFORE_ACTIVITY = "archive-before-activity"
    EARLY_ARCHIVE = "early-archive"


@dataclass(frozen=True, slots=True)
class ArchiveRecoveryDecision:
    """Pure decision about whether one archived transition may be reversed."""

    reason: ArchiveRecoveryReason
    seconds_early: float | None = None

    @property
    def should_reopen(self) -> bool:
        return self.reason == ArchiveRecoveryReason.LIKELY_AUTOMATIC


def evaluate_active_archive_recovery(
    baseline: ActiveThreadBaseline,
    current: ActiveThreadBaseline,
) -> ArchiveRecoveryDecision:
    """Classify an archive using Discord timestamps from an initially active thread."""
    duration_seconds = baseline.auto_archive_duration_seconds
    if duration_seconds is None:
        return ArchiveRecoveryDecision(ArchiveRecoveryReason.DEADLINE_UNKNOWN)
    if current.auto_archive_duration_seconds != duration_seconds:
        return ArchiveRecoveryDecision(ArchiveRecoveryReason.DURATION_CHANGED)
    if baseline.locked is None or current.locked != baseline.locked:
        return ArchiveRecoveryDecision(ArchiveRecoveryReason.LOCK_CHANGED)
    if baseline.pinned is None or current.pinned != baseline.pinned:
        return ArchiveRecoveryDecision(ArchiveRecoveryReason.PIN_CHANGED)
    if baseline.pinned:
        return ArchiveRecoveryDecision(ArchiveRecoveryReason.PINNED)

    archived_at = current.archive_timestamp
    initial_archive_at = baseline.archive_timestamp
    initial_message_at = baseline.last_message_timestamp
    current_message_at = current.last_message_timestamp
    if (
        archived_at is None
        or initial_archive_at is None
        or initial_message_at is None
        or current_message_at is None
    ):
        return ArchiveRecoveryDecision(ArchiveRecoveryReason.ACTIVITY_UNKNOWN)

    latest_activity_at = max(
        initial_archive_at,
        initial_message_at,
        current_message_at,
    )
    if archived_at < latest_activity_at:
        return ArchiveRecoveryDecision(
            ArchiveRecoveryReason.ARCHIVE_BEFORE_ACTIVITY
        )

    elapsed = (archived_at - latest_activity_at).total_seconds()
    seconds_early = duration_seconds - elapsed
    if elapsed + _AUTO_ARCHIVE_EARLY_TOLERANCE_SECONDS < duration_seconds:
        return ArchiveRecoveryDecision(
            ArchiveRecoveryReason.EARLY_ARCHIVE,
            seconds_early=seconds_early,
        )
    return ArchiveRecoveryDecision(ArchiveRecoveryReason.LIKELY_AUTOMATIC)


def evaluate_activated_archive_recovery(
    activation: ArchivedThreadActivation,
    current: ActiveThreadBaseline,
    *,
    elapsed_seconds: float | None,
) -> ArchiveRecoveryDecision:
    """Classify an archive using the monotonic clock from DMD's activation."""
    duration_seconds = activation.auto_archive_duration_seconds
    if duration_seconds is None or elapsed_seconds is None:
        return ArchiveRecoveryDecision(ArchiveRecoveryReason.DEADLINE_UNKNOWN)
    if current.auto_archive_duration_seconds != duration_seconds:
        return ArchiveRecoveryDecision(ArchiveRecoveryReason.DURATION_CHANGED)
    if activation.locked is None or current.locked != activation.locked:
        return ArchiveRecoveryDecision(ArchiveRecoveryReason.LOCK_CHANGED)

    elapsed = max(0.0, elapsed_seconds)
    seconds_early = duration_seconds - elapsed
    if elapsed + _AUTO_ARCHIVE_EARLY_TOLERANCE_SECONDS < duration_seconds:
        return ArchiveRecoveryDecision(
            ArchiveRecoveryReason.EARLY_ARCHIVE,
            seconds_early=seconds_early,
        )
    return ArchiveRecoveryDecision(ArchiveRecoveryReason.LIKELY_AUTOMATIC)
