"""Owned-thread deletion policy, impact scanning, and fallback decisions."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..discord.channel_types import is_thread_channel
from ..discord.formatting import channel_str
from ..discord.models import DeleteOutcome, DiscordChannel, DiscordMessage
from ..logging import structured_event
from .models import (
    ForeignReactionImpact,
    OwnedThreadDeletionOutcome,
    ThreadDeletionImpact,
)
from .planner import CleanupPlanner


CompleteThreadFetcher = Callable[[], list[DiscordMessage]]
ThreadImpactReporter = Callable[[DiscordChannel, ThreadDeletionImpact], None]


class OwnedThreadDeletionCoordinator:
    """Replace ordinary cleanup with opt-in deletion of creator-owned threads."""

    def __init__(
        self,
        *,
        api: Any,
        user_id: str,
        logger: Any,
        report_impact: ThreadImpactReporter,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api = api
        self.user_id = str(user_id)
        self.logger = logger
        self._report_impact = report_impact
        self._clock = clock

    def prepare(
        self,
        *,
        channel: DiscordChannel,
        mode: str,
        dry_run: bool,
        fetch_complete_history: CompleteThreadFetcher,
    ) -> OwnedThreadDeletionOutcome:
        """Plan or execute deletion of one creator-owned thread, with fallback."""
        if mode not in {"none", "self-only", "all"}:
            raise ValueError(
                "delete_owned_threads must be 'none', 'self-only', or 'all'."
            )
        if mode == "none" or not is_thread_channel(channel.get("type")):
            return OwnedThreadDeletionOutcome()

        owner_id = channel.get("owner_id")
        if owner_id is None:
            self.logger.diagnostic(
                "Skipping owned thread deletion for %s because Discord omitted owner_id.",
                channel_str(channel),
            )
            return OwnedThreadDeletionOutcome()
        if str(owner_id) != self.user_id:
            self.logger.diagnostic(
                "Skipping owned thread deletion for %s because it was created by another user.",
                channel_str(channel),
            )
            return OwnedThreadDeletionOutcome()

        if mode == "all" and not dry_run:
            return self._attempt_deletion(
                channel=channel,
                mode=mode,
                dry_run=False,
            )

        scan_started_at = self._clock()
        if mode == "self-only":
            self.logger.info(
                "Scanning complete history before self-only deletion of %s.",
                channel_str(channel),
            )
        else:
            self.logger.info(
                "Scanning complete history to report deletion impact for %s.",
                channel_str(channel),
            )
        scanned_messages = tuple(fetch_complete_history())
        scan_elapsed = self._clock() - scan_started_at
        impact = self._build_impact(
            channel=channel,
            messages=scanned_messages,
        )
        self._report_impact(channel, impact)

        if mode == "all":
            return self._attempt_deletion(
                channel=channel,
                mode=mode,
                dry_run=dry_run,
                impact=impact,
            )

        fallback = OwnedThreadDeletionOutcome(
            scanned_messages=scanned_messages,
            scan_elapsed=scan_elapsed,
        )
        if not impact.scan_complete:
            self.logger.warning(
                "Skipping self-only thread deletion for %s because a complete history scan could not be proven.",
                channel_str(channel),
            )
            return fallback

        if impact.foreign_messages:
            self.logger.info(
                "Skipping self-only thread deletion for %s because it contains %s message(s) "
                "from other or unknown authors.",
                channel_str(channel),
                impact.foreign_messages,
            )
            return fallback

        outcome = self._attempt_deletion(
            channel=channel,
            mode=mode,
            dry_run=dry_run,
            impact=impact,
        )
        if outcome.terminal:
            return outcome
        return OwnedThreadDeletionOutcome(
            failed=outcome.failed,
            scanned_messages=scanned_messages,
            scan_elapsed=scan_elapsed,
        )

    def _attempt_deletion(
        self,
        *,
        channel: DiscordChannel,
        mode: str,
        dry_run: bool,
        impact: ThreadDeletionImpact | None = None,
    ) -> OwnedThreadDeletionOutcome:
        impact_description = (
            "including messages and reactions from other users"
            if mode == "all"
            else "after finding no messages from other authors in the completed scan"
        )
        event = structured_event(
            "cleanup.action",
            mode="dry-run" if dry_run else "execute",
            artifact="thread",
            action="delete",
            count=1,
            delete_mode=mode,
        )
        if dry_run:
            self.logger.event(
                "Would delete owned thread %s, %s.",
                channel_str(channel),
                impact_description,
                indent=1,
                prefix="-",
                extra=event,
            )
            return OwnedThreadDeletionOutcome(
                terminal=True,
                planned=True,
                impact=impact,
            )

        self.logger.event(
            "Deleting owned thread %s, %s.",
            channel_str(channel),
            impact_description,
            indent=1,
            prefix="-",
            extra=event,
        )
        delete_outcome = self.api.delete_thread(channel["id"])
        if delete_outcome == DeleteOutcome.DELETED:
            return OwnedThreadDeletionOutcome(
                terminal=True,
                deleted=True,
                impact=impact,
            )
        if delete_outcome == DeleteOutcome.ABSENT:
            return OwnedThreadDeletionOutcome(
                terminal=True,
                absent=True,
            )

        self.logger.warning(
            "Owned thread deletion failed for %s; falling back to ordinary message and reaction cleanup.",
            channel_str(channel),
        )
        return OwnedThreadDeletionOutcome(failed=True)

    def _build_impact(
        self,
        *,
        channel: DiscordChannel,
        messages: tuple[DiscordMessage, ...],
    ) -> ThreadDeletionImpact:
        """Summarize messages and dependent foreign reactions from one scan."""
        scan_complete = self._scan_is_complete(channel, messages)
        own_messages = sum(
            message.get("author_id") == self.user_id for message in messages
        )
        foreign_reactions = ForeignReactionImpact()
        for message in messages:
            foreign_reactions = foreign_reactions.combined_with(
                CleanupPlanner.foreign_reaction_impact(
                    message.get("reactions") or []
                )
            )
        if not scan_complete:
            foreign_reactions = ForeignReactionImpact(
                normal=foreign_reactions.normal,
                burst=foreign_reactions.burst,
                complete=False,
            )
        return ThreadDeletionImpact(
            own_messages=own_messages,
            foreign_messages=len(messages) - own_messages,
            foreign_reactions=foreign_reactions,
            scan_complete=scan_complete,
        )

    def _scan_is_complete(
        self,
        channel: DiscordChannel,
        messages: tuple[DiscordMessage, ...],
    ) -> bool:
        get_last_fetch_summary = getattr(self.api, "get_last_fetch_summary", None)
        fetch_summary = (
            get_last_fetch_summary(channel["id"])
            if callable(get_last_fetch_summary)
            else None
        )
        message_count = channel.get("message_count")
        return (
            isinstance(fetch_summary, dict)
            and fetch_summary.get("complete") is True
            and isinstance(message_count, int)
            and not isinstance(message_count, bool)
            and message_count >= 0
            and len(messages) >= message_count
        )
