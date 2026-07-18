from collections.abc import Mapping
from typing import Any, Optional

from ..discord.formatting import channel_str
from ..discord.models import DiscordChannel
from ..logging import StructuredLogValue, structured_event
from .models import (
    ChannelPlan,
    CleanupRunOptions,
    CleanupRunStats,
    ForeignReactionImpact,
    ThreadDeletionImpact,
)


class CleanupReporter:
    """Format and emit cleanup estimates, impact, and channel summaries."""

    def __init__(self, logger: Any):
        self.logger = logger

    def log_buffered_channel_pre_execution(
        self,
        buffer_elapsed: float,
        channel_plan: ChannelPlan,
        delete_sleep_time_range: tuple[float, float],
    ) -> None:
        self.logger.progress(
            "Buffered messages=%s, scan time=%s, est. execute time=%s.",
            channel_plan.buffered_message_count,
            self.format_duration(buffer_elapsed),
            self.format_duration(
                self.estimate_action_duration(
                    channel_plan,
                    delete_sleep_time_range,
                )
            ),
            indent=1,
            prefix="-",
        )

    @classmethod
    def estimate_action_duration(
        cls,
        channel_plan: ChannelPlan,
        delete_sleep_time_range: tuple[float, float],
    ) -> float:
        return cls.estimate_action_count_duration(
            channel_plan.action_count,
            delete_sleep_time_range,
        )

    @staticmethod
    def estimate_action_count_duration(
        action_count: int,
        delete_sleep_time_range: tuple[float, float],
    ) -> float:
        average_sleep = sum(delete_sleep_time_range) / 2
        return max(0, action_count - 1) * average_sleep

    @staticmethod
    def format_duration(seconds: float) -> str:
        whole_seconds = max(0, int(round(seconds)))
        hours, remainder = divmod(whole_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    @staticmethod
    def format_foreign_reaction_impact(
        impact: ForeignReactionImpact,
    ) -> str:
        if not impact.complete:
            return "unknown"
        return f"{impact.normal} normal / {impact.burst} super"

    @classmethod
    def format_foreign_reaction_stats(
        cls,
        stats: Mapping[str, int],
    ) -> str:
        return cls.format_foreign_reaction_impact(
            ForeignReactionImpact(
                normal=stats["foreign_reactions_normal_count"],
                burst=stats["foreign_reactions_burst_count"],
                complete=stats["foreign_reactions_unknown_count"] == 0,
            )
        )

    def log_thread_deletion_impact(
        self,
        channel: DiscordChannel,
        impact: ThreadDeletionImpact,
    ) -> None:
        """Report the deletion cascade observed during an owned-thread scan."""
        if impact.scan_complete:
            message_impact = (
                f"{impact.own_messages} yours / "
                f"{impact.foreign_messages} other-or-unknown"
            )
        else:
            message_impact = "unknown (incomplete thread scan)"
        self.logger.progress(
            "Impact at scan time for %s: messages %s; foreign reactions %s.",
            channel_str(channel),
            message_impact,
            self.format_foreign_reaction_impact(impact.foreign_reactions),
            indent=1,
            prefix="-",
        )

    def log_fetch_summary(self, fetch_summary: Optional[dict]) -> None:
        if not fetch_summary:
            return

        if fetch_summary.get("wait_count"):
            self.logger.diagnostic(
                "Waited %.2fs between fetch batches.",
                fetch_summary["waited_seconds"],
                indent=1,
                prefix="-",
            )

        self.logger.diagnostic(
            "Fetched %s messages (%s).",
            fetch_summary["fetched_count"],
            fetch_summary["stop_reason"],
            indent=1,
            prefix="-",
        )

    def format_channel_summary(
        self,
        stats: Mapping[str, int],
        delete_reactions: bool,
        dry_run: bool,
        channel_plan: Optional[ChannelPlan] = None,
    ) -> str:
        if dry_run:
            summary = (
                f"Summary: messages {stats['deleted_count']} delete / "
                f"{stats['preserved_deletable_count']} keep"
            )
            reaction_summary = (
                f", reactions {stats['reactions_removed_count']} delete / "
                f"{stats['preserved_reactions_count']} keep"
            )
        else:
            summary = (
                f"Summary: messages {stats['deleted_count']} deleted / "
                f"{stats.get('absent_count', 0)} absent / "
                f"{stats.get('failed_count', 0)} failed / "
                f"{stats['preserved_deletable_count']} kept"
            )
            reaction_summary = (
                f", reactions {stats['reactions_removed_count']} deleted / "
                f"{stats.get('reactions_absent_count', 0)} absent / "
                f"{stats.get('reactions_failed_count', 0)} failed / "
                f"{stats['preserved_reactions_count']} kept"
            )

        if delete_reactions:
            summary += reaction_summary
        elif dry_run:
            summary += ", reactions 0 delete / 0 keep"
        else:
            summary += ", reactions 0 deleted / 0 absent / 0 failed / 0 kept"

        if dry_run and channel_plan is not None:
            summary += (
                f", buffered messages={channel_plan.buffered_message_count}"
            )
        if stats.get("actions_not_attempted_count", 0):
            summary += (
                f", actions {stats['actions_not_attempted_count']} not attempted "
                "after thread state changed"
            )
        if dry_run and stats["deleted_count"]:
            summary += (
                ", foreign reactions affected "
                f"{self.format_foreign_reaction_stats(stats)}"
            )
        return summary

    @staticmethod
    def _summary_event(
        stats: Mapping[str, int],
        *,
        scope: str,
        delete_reactions: bool,
        dry_run: bool,
        buffered_messages: int | None = None,
    ) -> dict[str, object]:
        data: dict[str, StructuredLogValue] = {
            "scope": scope,
            "mode": "dry-run" if dry_run else "execute",
            "foreign_reactions_normal": stats.get(
                "foreign_reactions_normal_count",
                0,
            ),
            "foreign_reactions_burst": stats.get(
                "foreign_reactions_burst_count",
                0,
            ),
            "foreign_reactions_complete": (
                stats.get("foreign_reactions_unknown_count", 0) == 0
            ),
        }
        if dry_run:
            data.update({
                "messages_delete": stats["deleted_count"],
                "messages_keep": stats["preserved_deletable_count"],
                "reactions_delete": (
                    stats["reactions_removed_count"]
                    if delete_reactions
                    else 0
                ),
                "reactions_keep": (
                    stats["preserved_reactions_count"]
                    if delete_reactions
                    else 0
                ),
            })
        else:
            data.update({
                "messages_deleted": stats["deleted_count"],
                "messages_absent": stats.get("absent_count", 0),
                "messages_failed": stats.get("failed_count", 0),
                "messages_kept": stats["preserved_deletable_count"],
                "reactions_deleted": (
                    stats["reactions_removed_count"]
                    if delete_reactions
                    else 0
                ),
                "reactions_absent": (
                    stats.get("reactions_absent_count", 0)
                    if delete_reactions
                    else 0
                ),
                "reactions_failed": (
                    stats.get("reactions_failed_count", 0)
                    if delete_reactions
                    else 0
                ),
                "reactions_kept": (
                    stats["preserved_reactions_count"]
                    if delete_reactions
                    else 0
                ),
            })
        if buffered_messages is not None:
            data["buffered_messages"] = buffered_messages
        if scope == "run":
            if dry_run:
                data["owned_threads_delete"] = stats.get(
                    "threads_planned_count",
                    0,
                )
            else:
                data.update({
                    "owned_threads_deleted": stats.get(
                        "threads_deleted_count",
                        0,
                    ),
                    "owned_threads_absent": stats.get(
                        "threads_absent_count",
                        0,
                    ),
                    "owned_threads_failed": stats.get(
                        "threads_failed_count",
                        0,
                    ),
                })
        return structured_event("cleanup.summary", **data)

    def log_dry_run_channel_summary(
        self,
        stats: Mapping[str, int],
        fetch_summary: Optional[dict],
        channel_elapsed: float,
        channel_execute_estimate: str,
        channel_total_estimate: str,
        delete_reactions: bool,
        channel_plan: Optional[ChannelPlan] = None,
    ) -> None:
        self.log_fetch_summary(fetch_summary)
        self.logger.progress(
            self.format_channel_summary(
                stats=stats,
                delete_reactions=delete_reactions,
                dry_run=True,
                channel_plan=channel_plan,
            ),
            indent=1,
            prefix="-",
            extra=self._summary_event(
                stats,
                scope="channel",
                delete_reactions=delete_reactions,
                dry_run=True,
                buffered_messages=(
                    channel_plan.buffered_message_count
                    if channel_plan is not None
                    else None
                ),
            ),
        )
        self.logger.progress(
            "scan time=%s, est. execute time=%s, est. total time=%s",
            self.format_duration(channel_elapsed),
            channel_execute_estimate,
            channel_total_estimate,
            indent=2,
        )

    def log_executed_channel_summary(
        self,
        stats: Mapping[str, int],
        fetch_summary: Optional[dict],
        channel_elapsed: float,
        action_elapsed: float,
        delete_reactions: bool,
        channel_plan: Optional[ChannelPlan] = None,
    ) -> None:
        self.log_fetch_summary(fetch_summary)
        self.logger.progress(
            self.format_channel_summary(
                stats=stats,
                delete_reactions=delete_reactions,
                dry_run=False,
                channel_plan=channel_plan,
            ),
            indent=1,
            prefix="-",
            extra=self._summary_event(
                stats,
                scope="channel",
                delete_reactions=delete_reactions,
                dry_run=False,
                buffered_messages=(
                    channel_plan.buffered_message_count
                    if channel_plan is not None
                    else None
                ),
            ),
        )
        if channel_plan is not None:
            self.logger.progress(
                "execute time=%s, total time=%s",
                self.format_duration(action_elapsed),
                self.format_duration(channel_elapsed),
                indent=2,
            )
        else:
            self.logger.progress(
                "total time=%s",
                self.format_duration(channel_elapsed),
                indent=2,
            )

    def log_run_summary(
        self,
        stats: CleanupRunStats,
        options: CleanupRunOptions,
        run_elapsed: float,
    ) -> None:
        if options.dry_run:
            execute_estimate_seconds = self.estimate_action_count_duration(
                stats.deleted_count
                + stats.reactions_removed_count
                + stats.threads_planned_count
                + 2 * stats.archived_threads_planned_count,
                options.delete_sleep_time_range,
            )
            total_summary = (
                f"Summary: messages {stats.deleted_count} delete / "
                f"{stats.preserved_deletable_count} keep"
            )
            if options.delete_reactions:
                total_summary += (
                    f", reactions {stats.reactions_removed_count} delete / "
                    f"{stats.preserved_reactions_count} keep"
                )
            if options.delete_owned_threads != "none":
                total_summary += (
                    f", owned threads {stats.threads_planned_count} delete"
                )
            if stats.threads_planned_count:
                if stats.foreign_messages_unknown_count:
                    total_summary += ", foreign messages affected unknown"
                else:
                    total_summary += (
                        ", foreign messages affected "
                        f"{stats.foreign_messages_affected_count}"
                    )
            if stats.deleted_count or stats.threads_planned_count:
                total_summary += (
                    ", foreign reactions affected "
                    f"{self.format_foreign_reaction_stats(stats)}"
                )
            self.logger.info(
                total_summary,
                extra=self._summary_event(
                    stats,
                    scope="run",
                    delete_reactions=options.delete_reactions,
                    dry_run=True,
                ),
            )
            self.logger.info(
                "scan time=%s, est. execute time=%s, est. total time=%s",
                self.format_duration(run_elapsed),
                self.format_duration(execute_estimate_seconds),
                self.format_duration(
                    run_elapsed + execute_estimate_seconds
                ),
            )
        else:
            total_summary = (
                f"Summary: messages {stats.deleted_count} deleted / "
                f"{stats.absent_count} absent / "
                f"{stats.failed_count} failed / "
                f"{stats.preserved_deletable_count} kept"
            )
            if options.delete_reactions:
                total_summary += (
                    f", reactions {stats.reactions_removed_count} deleted / "
                    f"{stats.reactions_absent_count} absent / "
                    f"{stats.reactions_failed_count} failed / "
                    f"{stats.preserved_reactions_count} kept"
                )
            if options.delete_owned_threads != "none":
                total_summary += (
                    f", owned threads {stats.threads_deleted_count} deleted / "
                    f"{stats.threads_absent_count} absent / "
                    f"{stats.threads_failed_count} failed"
                )
            self.logger.info(
                total_summary,
                extra=self._summary_event(
                    stats,
                    scope="run",
                    delete_reactions=options.delete_reactions,
                    dry_run=False,
                ),
            )

        archived_summary_parts = []
        if stats.archived_threads_skipped_count:
            archived_summary_parts.append(
                f"{stats.archived_threads_skipped_count} skipped without content scan"
            )
        if stats.archived_threads_planned_count:
            archived_summary_parts.append(
                f"{stats.archived_threads_planned_count} with cleanup actions"
            )
        if stats.archived_threads_opened_count:
            archived_summary_parts.append(
                f"{stats.archived_threads_opened_count} unarchived"
            )
        if stats.archived_threads_restored_count:
            archived_summary_parts.append(
                f"{stats.archived_threads_restored_count} restored"
            )
        if stats.archived_threads_absent_count:
            archived_summary_parts.append(
                f"{stats.archived_threads_absent_count} absent during restoration"
            )
        if stats.archived_threads_open_failed_count:
            archived_summary_parts.append(
                f"{stats.archived_threads_open_failed_count} unarchive failed"
            )
        if stats.archived_threads_left_active_count:
            archived_summary_parts.append(
                f"{stats.archived_threads_left_active_count} left active"
            )
        if stats.archived_threads_auto_reopened_count:
            archived_summary_parts.append(
                f"{stats.archived_threads_auto_reopened_count} reopened after likely auto-archive"
            )
        if stats.archived_threads_interrupted_count:
            archived_summary_parts.append(
                f"{stats.archived_threads_interrupted_count} interrupted by archive state changes"
            )
        if stats.archived_thread_actions_not_attempted_count:
            archived_summary_parts.append(
                f"{stats.archived_thread_actions_not_attempted_count} actions not attempted"
            )
        if archived_summary_parts:
            self.logger.info(
                "Archived threads: %s.",
                " / ".join(archived_summary_parts),
            )
