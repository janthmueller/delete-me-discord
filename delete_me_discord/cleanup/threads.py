from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Sequence, cast

from ..discord.formatting import channel_str
from ..discord.models import DiscordChannel, UpdateOutcome
from ..discord.errors import ReachedMaxRetries, ResourceUnavailable, UnexpectedStatus
from ..privacy import sensitive
from ..storage import atomic_write_json


ARCHIVED_THREAD_CLEANUP_MODES = ("skip", "temporary", "allow-active")
DEFAULT_THREAD_RESTORATION_PATH = os.path.join(
    os.path.expanduser("~"),
    ".config",
    "delete-me-discord",
    "thread-restoration.json",
)

ADMINISTRATOR_PERMISSION = 1 << 3
VIEW_CHANNEL_PERMISSION = 1 << 10
SEND_MESSAGES_PERMISSION = 1 << 11
MANAGE_THREADS_PERMISSION = 1 << 34
SEND_MESSAGES_IN_THREADS_PERMISSION = 1 << 38
_RELEVANT_PERMISSION_MASK = (
    ADMINISTRATOR_PERMISSION
    | VIEW_CHANNEL_PERMISSION
    | SEND_MESSAGES_PERMISSION
    | MANAGE_THREADS_PERMISSION
    | SEND_MESSAGES_IN_THREADS_PERMISSION
)
_AUTO_ARCHIVE_EARLY_TOLERANCE_SECONDS = 30.0


def _permission_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def effective_channel_permissions(
    *,
    guild_id: str,
    guild_owner: bool,
    guild_permissions: Any,
    member_role_ids: Sequence[str],
    user_id: str,
    permission_overwrites: Any,
) -> int | None:
    """Apply Discord's documented channel-overwrite precedence to guild permissions."""
    if guild_owner:
        return _RELEVANT_PERMISSION_MASK

    permissions = _permission_value(guild_permissions)
    if permissions is None:
        return None
    if permissions & ADMINISTRATOR_PERMISSION:
        return permissions | _RELEVANT_PERMISSION_MASK
    if not isinstance(permission_overwrites, list):
        return None

    role_ids = {str(role_id) for role_id in member_role_ids}
    everyone_overwrite: tuple[int, int] | None = None
    role_allow = 0
    role_deny = 0
    member_overwrite: tuple[int, int] | None = None

    for overwrite in permission_overwrites:
        if not isinstance(overwrite, Mapping):
            return None
        overwrite_id = overwrite.get("id")
        overwrite_type = overwrite.get("type")
        allow = _permission_value(overwrite.get("allow"))
        deny = _permission_value(overwrite.get("deny"))
        if (
            overwrite_id is None
            or overwrite_type is None
            or allow is None
            or deny is None
        ):
            return None
        try:
            overwrite_type = int(overwrite_type)
        except (TypeError, ValueError):
            return None
        overwrite_id = str(overwrite_id)

        if overwrite_type == 0 and overwrite_id == guild_id:
            everyone_overwrite = (allow, deny)
        elif overwrite_type == 0 and overwrite_id in role_ids:
            role_allow |= allow
            role_deny |= deny
        elif overwrite_type == 1 and overwrite_id == user_id:
            member_overwrite = (allow, deny)
        elif overwrite_type not in {0, 1}:
            return None

    if everyone_overwrite is not None:
        allow, deny = everyone_overwrite
        permissions &= ~deny
        permissions |= allow

    permissions &= ~role_deny
    permissions |= role_allow

    if member_overwrite is not None:
        allow, deny = member_overwrite
        permissions &= ~deny
        permissions |= allow

    return permissions


@dataclass(frozen=True, slots=True)
class ArchivedThreadAssessment:
    """Pre-scan decision for one archived thread."""

    should_scan: bool
    restore_expected: bool = False
    restoration_status: str = "unavailable"
    reason: str = ""


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
class ArchivedThreadResumeResult:
    """State transition result after a delete finds the thread archived."""

    retry_action: bool
    activation: ArchivedThreadActivation


class ThreadRestoreOutcome(Enum):
    RESTORED = "restored"
    ABSENT = "absent"
    LEFT_ACTIVE = "left-active"


class ThreadRestorationJournal:
    """Persist threads that must be re-archived after an interrupted cleanup."""

    VERSION = 1

    def __init__(self, path: str = DEFAULT_THREAD_RESTORATION_PATH) -> None:
        self.path = path

    def pending(self, user_id: str) -> tuple[str, ...]:
        return tuple(
            entry["thread_id"]
            for entry in self._entries()
            if entry["user_id"] == user_id
        )

    def record(self, user_id: str, thread_id: str) -> None:
        entries = self._entries()
        entry = {"user_id": str(user_id), "thread_id": str(thread_id)}
        if entry in entries:
            return
        entries.append(entry)
        self._write(entries)

    def clear(self, user_id: str, thread_id: str) -> None:
        entries = self._entries()
        retained = [
            entry
            for entry in entries
            if not (
                entry["user_id"] == str(user_id)
                and entry["thread_id"] == str(thread_id)
            )
        ]
        if retained != entries:
            self._write(retained)

    def _entries(self) -> list[dict[str, str]]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict) or payload.get("version") != self.VERSION:
            raise ValueError("Thread restoration journal has an unsupported format.")
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            raise ValueError("Thread restoration journal entries must be a list.")
        entries: list[dict[str, str]] = []
        for entry in raw_entries:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("user_id"), str)
                or not isinstance(entry.get("thread_id"), str)
            ):
                raise ValueError("Thread restoration journal contains an invalid entry.")
            entries.append({
                "user_id": entry["user_id"],
                "thread_id": entry["thread_id"],
            })
        return entries

    def _write(self, entries: list[dict[str, str]]) -> None:
        atomic_write_json(
            self.path,
            {"version": self.VERSION, "entries": entries},
        )


class ArchivedThreadCoordinator:
    """Resolve archived-thread capabilities and guard temporary state transitions."""

    def __init__(
        self,
        *,
        api: Any,
        user_id: str,
        journal: ThreadRestorationJournal | None,
        logger: Any,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api = api
        self.user_id = str(user_id)
        self.journal = journal
        self.logger = logger
        self._clock = clock
        self._member_roles_by_guild: dict[str, tuple[str, ...] | None] = {}

    def assess(
        self,
        *,
        channel: DiscordChannel,
        guild: Mapping[str, Any] | None,
        parent: DiscordChannel | None,
        mode: str,
    ) -> ArchivedThreadAssessment:
        if mode not in ARCHIVED_THREAD_CLEANUP_MODES:
            raise ValueError(
                "archived_thread_cleanup must be 'skip', 'temporary', or 'allow-active'."
            )
        if mode == "skip":
            return ArchivedThreadAssessment(
                should_scan=False,
                reason="archived thread cleanup is disabled",
            )

        metadata = channel.get("thread_metadata")
        locked = isinstance(metadata, Mapping) and metadata.get("locked") is True
        creator = str(channel.get("owner_id")) == self.user_id
        manage_threads: bool | None = None
        if locked or not creator:
            manage_threads = self._has_manage_threads(guild=guild, parent=parent)

        restore_expected = creator or manage_threads is True
        if restore_expected:
            restoration_status = "available"
        elif manage_threads is False:
            restoration_status = "unavailable"
        else:
            restoration_status = "unknown"

        if locked and manage_threads is not True:
            reason = (
                "locked thread requires MANAGE_THREADS"
                if manage_threads is False
                else "locked thread permission could not be established"
            )
            return ArchivedThreadAssessment(
                should_scan=False,
                restoration_status=restoration_status,
                reason=reason,
            )

        if mode == "temporary" and not restore_expected:
            reason = (
                "thread cannot be restored by this user"
                if restoration_status == "unavailable"
                else "thread restoration permission could not be established"
            )
            return ArchivedThreadAssessment(
                should_scan=False,
                restoration_status=restoration_status,
                reason=reason,
            )

        return ArchivedThreadAssessment(
            should_scan=True,
            restore_expected=restore_expected,
            restoration_status=restoration_status,
        )

    def activate(
        self,
        channel: DiscordChannel,
        assessment: ArchivedThreadAssessment,
    ) -> ArchivedThreadActivation:
        thread_id = str(channel["id"])
        journaled = False
        if assessment.restore_expected:
            if self.journal is None:
                self.logger.error(
                    "Skipping temporary activation of %s because restoration journaling is unavailable.",
                    channel_str(channel),
                )
                return ArchivedThreadActivation(False, True)
            self.journal.record(self.user_id, thread_id)
            journaled = True

        try:
            outcome = self.api.set_thread_archived(thread_id, archived=False)
        except Exception:
            # The request may have reached Discord even when its response was lost.
            # Keep any recovery entry and let the caller surface the request failure.
            raise
        if outcome != UpdateOutcome.APPLIED:
            if journaled and self.journal is not None:
                self.journal.clear(self.user_id, thread_id)
            return ArchivedThreadActivation(False, assessment.restore_expected)

        self.logger.event(
            "Temporarily unarchived thread %s for cleanup.",
            channel_str(channel),
            indent=1,
            prefix="-",
        )
        return ArchivedThreadActivation(
            opened=True,
            restore_expected=assessment.restore_expected,
            journaled=journaled,
            activated_at=self._clock(),
            auto_archive_duration_seconds=self._auto_archive_duration_seconds(
                channel
            ),
            locked=self._locked_state(channel),
        )

    def resume_after_likely_auto_archive(
        self,
        channel: DiscordChannel,
        activation: ArchivedThreadActivation,
    ) -> ArchivedThreadResumeResult:
        """Reopen after a likely automatic timeout, without fighting other state changes."""
        thread_id = str(channel["id"])
        get_channel = getattr(self.api, "get_channel", None)
        if not callable(get_channel):
            self.logger.warning(
                "Stopping cleanup in thread %s because its archive state cannot be verified.",
                channel_str(channel),
            )
            return ArchivedThreadResumeResult(False, activation)

        try:
            current = get_channel(thread_id)
        except (ReachedMaxRetries, ResourceUnavailable, UnexpectedStatus) as exc:
            self.logger.warning(
                "Stopping cleanup in thread %s because its archive state could not be refreshed: %s",
                channel_str(channel),
                exc,
            )
            return ArchivedThreadResumeResult(False, activation)

        if not isinstance(current, Mapping) or str(current.get("id")) != thread_id:
            self.logger.warning(
                "Stopping cleanup in thread %s because Discord returned an invalid state payload.",
                channel_str(channel),
            )
            return ArchivedThreadResumeResult(False, activation)

        metadata = current.get("thread_metadata")
        if not isinstance(metadata, Mapping) or metadata.get("archived") is not True:
            self.logger.warning(
                "Stopping cleanup in thread %s because Discord no longer reports it as archived.",
                channel_str(channel),
            )
            return ArchivedThreadResumeResult(False, activation)

        closed_activation = self._observed_archived(activation, thread_id)
        duration_seconds = activation.auto_archive_duration_seconds
        activated_at = activation.activated_at
        current_channel = cast(DiscordChannel, current)
        current_duration_seconds = self._auto_archive_duration_seconds(current_channel)
        current_locked = self._locked_state(current_channel)
        if duration_seconds is None or activated_at is None:
            self.logger.warning(
                "Stopping cleanup in thread %s because its auto-archive deadline is unknown.",
                channel_str(channel),
            )
            return ArchivedThreadResumeResult(False, closed_activation)
        if current_duration_seconds != duration_seconds:
            self.logger.warning(
                "Stopping cleanup in thread %s because its auto-archive duration changed during cleanup.",
                channel_str(channel),
            )
            return ArchivedThreadResumeResult(False, closed_activation)
        if activation.locked is None or current_locked != activation.locked:
            self.logger.warning(
                "Stopping cleanup in thread %s because its lock state changed or could not be verified.",
                channel_str(channel),
            )
            return ArchivedThreadResumeResult(False, closed_activation)

        elapsed = max(0.0, self._clock() - activated_at)
        if elapsed + _AUTO_ARCHIVE_EARLY_TOLERANCE_SECONDS < duration_seconds:
            self.logger.warning(
                "Stopping cleanup in thread %s because it archived %.0f seconds before its configured auto-archive deadline; treating this as an external state change.",
                channel_str(channel),
                duration_seconds - elapsed,
            )
            return ArchivedThreadResumeResult(False, closed_activation)

        self.logger.event(
            "Thread %s reached its likely auto-archive deadline; reopening it to resume cleanup.",
            channel_str(channel),
            indent=1,
            prefix="-",
        )
        resumed = self.activate(
            current_channel,
            ArchivedThreadAssessment(
                should_scan=True,
                restore_expected=activation.restore_expected,
                restoration_status=(
                    "available" if activation.restore_expected else "unavailable"
                ),
            ),
        )
        if not resumed.opened:
            self.logger.warning(
                "Stopping cleanup in thread %s because automatic reactivation failed.",
                channel_str(channel),
            )
            return ArchivedThreadResumeResult(False, closed_activation)
        return ArchivedThreadResumeResult(True, resumed)

    def restore(
        self,
        channel: DiscordChannel,
        activation: ArchivedThreadActivation,
    ) -> ThreadRestoreOutcome:
        thread_id = str(channel["id"])
        try:
            outcome = self.api.set_thread_archived(thread_id, archived=True)
        except Exception as exc:
            self._log_restore_failure(channel, activation, str(exc))
            return ThreadRestoreOutcome.LEFT_ACTIVE

        if outcome == UpdateOutcome.APPLIED:
            if activation.journaled and self.journal is not None:
                self.journal.clear(self.user_id, thread_id)
            self.logger.event(
                "Restored thread %s to archived state.",
                channel_str(channel),
                indent=1,
                prefix="-",
            )
            return ThreadRestoreOutcome.RESTORED
        if outcome == UpdateOutcome.ABSENT:
            if activation.journaled and self.journal is not None:
                self.journal.clear(self.user_id, thread_id)
            return ThreadRestoreOutcome.ABSENT

        self._log_restore_failure(channel, activation, "Discord rejected the update")
        return ThreadRestoreOutcome.LEFT_ACTIVE

    def restore_pending(self) -> tuple[int, int]:
        if self.journal is None:
            return 0, 0
        restored = 0
        failed = 0
        for thread_id in self.journal.pending(self.user_id):
            self.logger.warning(
                "Retrying interrupted restoration of thread %s.",
                sensitive(thread_id),
            )
            try:
                outcome = self.api.set_thread_archived(thread_id, archived=True)
            except Exception as exc:
                self.logger.error(
                    "Could not restore thread %s; recovery entry retained: %s",
                    sensitive(thread_id),
                    exc,
                )
                failed += 1
                continue
            if outcome.desired_state_reached:
                self.journal.clear(self.user_id, thread_id)
                restored += 1
            else:
                failed += 1
        return restored, failed

    def _has_manage_threads(
        self,
        *,
        guild: Mapping[str, Any] | None,
        parent: DiscordChannel | None,
    ) -> bool | None:
        if guild is None or parent is None:
            return None
        guild_id_value = guild.get("id")
        if guild_id_value is None:
            return None
        guild_id = str(guild_id_value)
        guild_permissions = _permission_value(guild.get("permissions"))
        if guild.get("owner") is True:
            return True
        if guild_permissions is not None and guild_permissions & ADMINISTRATOR_PERMISSION:
            return True

        role_ids = self._member_roles(guild_id)
        if role_ids is None:
            return None
        effective = effective_channel_permissions(
            guild_id=guild_id,
            guild_owner=False,
            guild_permissions=guild.get("permissions"),
            member_role_ids=role_ids,
            user_id=self.user_id,
            permission_overwrites=parent.get("permission_overwrites"),
        )
        if effective is None:
            return None
        return bool(effective & MANAGE_THREADS_PERMISSION)

    def _member_roles(self, guild_id: str) -> tuple[str, ...] | None:
        if guild_id in self._member_roles_by_guild:
            return self._member_roles_by_guild[guild_id]
        get_member = getattr(self.api, "get_current_guild_member", None)
        if not callable(get_member):
            self._member_roles_by_guild[guild_id] = None
            return None
        try:
            member = get_member(guild_id)
        except (ReachedMaxRetries, ResourceUnavailable, UnexpectedStatus) as exc:
            self.logger.diagnostic(
                "Could not resolve current member roles for guild %s: %s",
                sensitive(guild_id),
                exc,
            )
            self._member_roles_by_guild[guild_id] = None
            return None
        roles = member.get("roles") if isinstance(member, Mapping) else None
        if not isinstance(roles, list) or any(role_id is None for role_id in roles):
            self._member_roles_by_guild[guild_id] = None
            return None
        normalized = tuple(str(role_id) for role_id in roles)
        self._member_roles_by_guild[guild_id] = normalized
        return normalized

    def _log_restore_failure(
        self,
        channel: DiscordChannel,
        activation: ArchivedThreadActivation,
        reason: str,
    ) -> None:
        if activation.restore_expected:
            self.logger.error(
                "Thread %s may remain active after cleanup; restoration failed and its recovery entry was retained (%s).",
                channel_str(channel),
                reason,
            )
            return
        self.logger.warning(
            "Thread %s remains active after cleanup; restoration was not guaranteed (%s).",
            channel_str(channel),
            reason,
        )

    def _observed_archived(
        self,
        activation: ArchivedThreadActivation,
        thread_id: str,
    ) -> ArchivedThreadActivation:
        if activation.journaled and self.journal is not None:
            self.journal.clear(self.user_id, thread_id)
        return ArchivedThreadActivation(
            opened=False,
            restore_expected=activation.restore_expected,
            journaled=False,
            activated_at=activation.activated_at,
            auto_archive_duration_seconds=activation.auto_archive_duration_seconds,
            locked=activation.locked,
        )

    @staticmethod
    def _auto_archive_duration_seconds(
        channel: Mapping[str, Any],
    ) -> float | None:
        metadata = channel.get("thread_metadata")
        if not isinstance(metadata, Mapping):
            return None
        duration = metadata.get("auto_archive_duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            return None
        if duration <= 0:
            return None
        return float(duration) * 60.0

    @staticmethod
    def _locked_state(channel: Mapping[str, Any]) -> bool | None:
        metadata = channel.get("thread_metadata")
        if not isinstance(metadata, Mapping):
            return None
        locked = metadata.get("locked")
        return locked if isinstance(locked, bool) else None
