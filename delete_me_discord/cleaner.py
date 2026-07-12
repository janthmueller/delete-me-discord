# delete_me_discord/cleaner.py
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Iterable, List, Generator, Tuple, Optional, Union, Set
import logging

from .api import DiscordAPI
from .channel_types import (
    is_archived_thread,
    is_thread_channel,
)
from .models import (
    ActionKind,
    ChannelPlan,
    DeleteOutcome,
    DiscordChannel,
    DiscordEmoji,
    DiscordMessage,
    DiscordReaction,
    ForeignReactionImpact,
    MessageDecision,
    MessageFacts,
    OwnedReaction,
    PlannedAction,
)
from .utils import channel_str, format_timestamp
from .privacy import sensitive
from .preserve_cache import PreserveCache
from .rate_limits import DELETE_POLICY, FETCH_POLICY
from .scope_filter import ScopeFilter
from .scope_inventory import ScopeDiscoverySeed, ScopeInventory, iter_cleanup_channels
from .scope_rules import ScopeRules
from .type_enums import ReactionType


@dataclass(frozen=True, slots=True)
class _OwnedThreadDeletionOutcome:
    """Result of optionally replacing message cleanup with one thread deletion."""

    terminal: bool = False
    planned: bool = False
    deleted: bool = False
    absent: bool = False
    failed: bool = False
    cleanup_messages: Optional[List[DiscordMessage]] = None
    scan_elapsed: Optional[float] = None
    impact: Optional["_ThreadDeletionImpact"] = None


@dataclass(frozen=True, slots=True)
class _ThreadDeletionImpact:
    """First-class and dependent artifacts affected by one thread deletion."""

    own_messages: int
    foreign_messages: int
    foreign_reactions: ForeignReactionImpact
    scan_complete: bool


@dataclass(slots=True)
class _DeleteActionCounts:
    """Counts for a group of concrete delete outcomes."""

    deleted: int = 0
    absent: int = 0
    failed: int = 0

    def record(self, outcome: DeleteOutcome) -> None:
        if outcome == DeleteOutcome.DELETED:
            self.deleted += 1
        elif outcome == DeleteOutcome.ABSENT:
            self.absent += 1
        else:
            self.failed += 1


class MessageCleaner:
    def __init__(
        self,
        api: DiscordAPI,
        user_id: Optional[str] = None,
        include_ids: Optional[List[str]] = None,
        exclude_ids: Optional[List[str]] = None,
        preserve_last: timedelta = timedelta(0),
        preserve_n: int = 0,
        preserve_n_mode: str = "all",
        preserve_cache: Optional[PreserveCache] = None,
        scope_inventory: Optional[ScopeInventory] = None,
        scope_seed: Optional[ScopeDiscoverySeed] = None,
        scope_filter: Optional[ScopeFilter] = None,
    ):
        """
        Initializes the MessageCleaner.

        Args:
            api (DiscordAPI): An instance of DiscordAPI.
            user_id (Optional[str]): The user ID whose messages will be targeted. If not provided and not set in the environment, it will be fetched via the API token.
            include_ids (Optional[List[str]]): IDs to include.
            exclude_ids (Optional[List[str]]): IDs to exclude.
            preserve_last (timedelta): Keep messages and reactions newer than this duration.
            preserve_n (int): Number of recent messages to keep in each channel.
            preserve_n_mode (str): How to count the last N messages to keep: 'mine' (only your deletable messages) or 'all' (all recent messages in the channel).
            preserve_cache (Optional[PreserveCache]): Optional cache to track preserved message IDs between runs.

        Raises:
            ValueError: If both include_ids and exclude_ids contain overlapping IDs.
            ValueError: If user_id cannot be resolved from arguments, environment variables, or the API token.
        """
        self.api = api
        self.user_id = user_id or os.getenv("DISCORD_USER_ID")
        if not self.user_id:
            try:
                current_user = self.api.get_current_user()
                self.user_id = current_user.get("id")
            except Exception:
                self.user_id = None
        if not self.user_id:
            raise ValueError("User ID not provided. Set DISCORD_USER_ID or ensure the token can fetch /users/@me.")

        self.scope_rules = ScopeRules.from_values(include_ids, exclude_ids)
        self.preserve_last = preserve_last
        self.preserve_n = preserve_n
        if preserve_n_mode not in {"mine", "all"}:
            raise ValueError("preserve_n_mode must be 'mine' or 'all'.")
        self.preserve_n_mode = preserve_n_mode
        self.logger = logging.getLogger(self.__class__.__name__)
        self.preserve_cache = preserve_cache
        self.scope_inventory = scope_inventory
        self.scope_seed = scope_seed
        self.scope_filter = scope_filter or (
            scope_inventory.scope_filter if scope_inventory else ScopeFilter()
        )

    def _configure_request_policy(
        self,
        name: str,
        interval: Tuple[float, float],
    ) -> None:
        configure = getattr(self.api, "configure_request_policy", None)
        if callable(configure):
            configure(name, interval)

    def get_all_channels(self) -> List[DiscordChannel]:
        """
        Collect all relevant channels based on include and exclude IDs.

        Returns:
            List[DiscordChannel]: Channels eligible for processing.
        """
        all_channels = list(self.iter_channels())
        self.logger.progress("Channels to process: %s", len(all_channels))
        return all_channels

    def iter_channels(self) -> Generator[DiscordChannel, None, None]:
        """Yield eligible channels without building a global cleanup inventory."""
        if self.scope_inventory is None:
            target_label = (
                "channels and threads"
                if self.scope_filter.thread_discovery_mode != "none"
                else "channels"
            )
            self.logger.progress("Discovering %s as cleanup advances.", target_label)
        for channel in iter_cleanup_channels(
            self.api,
            scope_filter=self.scope_filter,
            inventory=self.scope_inventory,
            seed=self.scope_seed,
        ):
            if not self._should_include_channel(channel):
                continue
            self.logger.debug("Included channel: %s.", channel_str(channel))
            yield channel

    def _should_include_channel(self, channel: DiscordChannel) -> bool:
        """
        Determines if a channel should be included based on include and exclude IDs.

        Args:
            channel (DiscordChannel): The channel payload.

        Returns:
            bool: True if the channel should be included, False otherwise.
        """
        allowed = self.scope_filter.includes_channel(channel) and self.scope_rules.includes(channel)
        if not allowed:
            self.logger.debug("Excluding channel based on include/exclude filters: %s.", channel_str(channel))
        return allowed

    def fetch_all_messages(
        self,
        channel: DiscordChannel,
        fetch_sleep_time_range: Tuple[float, float],
        fetch_since: Optional[datetime],
        max_messages: Union[int, float]
    ) -> Generator[DiscordMessage, None, None]:
        """
        Fetch all messages for one channel using the API fetch boundaries.

        Args:
            channel (DiscordChannel): The channel payload.
            fetch_sleep_time_range (Tuple[float, float]): Minimum interval between fetch requests.
            fetch_since (Optional[datetime]): Only fetch messages newer than this timestamp.
            max_messages (Union[int, float]): Maximum number of messages to fetch.

        Yields:
            DiscordMessage: Normalized message payload.
        """
        self.logger.debug("Fetching messages from %s.", channel_str(channel))
        fetched_count = 0

        for message in self.api.fetch_messages(
            channel["id"],
            fetch_sleep_time_range=fetch_sleep_time_range,
            fetch_since=fetch_since,
            max_messages=max_messages,
        ):
            yield message
            fetched_count += 1

        self.logger.debug("Fetched %s messages from %s.", fetched_count, channel_str(channel))

    def delete_messages_older_than(
        self,
        messages: Iterable[DiscordMessage],
        cutoff_time: datetime,
        delete_sleep_time_range: Tuple[float, float],
        dry_run: bool = False,
        delete_reactions: bool = False,
        channel_plan: Optional[ChannelPlan] = None,
    ) -> Tuple[List[str], dict[str, int], float]:
        """
        Execute planned actions for one channel and collect per-channel stats.

        Args:
            messages (Iterable[DiscordMessage]): Message data in newest-to-oldest order.
            cutoff_time (datetime): The cutoff datetime; messages older than this will be deleted.
            delete_sleep_time_range (Tuple[float, float]): Minimum interval between delete requests.
            dry_run (bool): If True, simulate deletions without calling the API.
            delete_reactions (bool): If True, remove the user's reactions on messages encountered.
            channel_plan (Optional[ChannelPlan]): Optional precomputed channel plan.

        Returns:
            Tuple[List[str], dict[str, int], float]: Preserved message IDs, statistics, and action-phase elapsed time.
        """
        preserved_msg_ids = []
        self._configure_request_policy(DELETE_POLICY, delete_sleep_time_range)
        stats = {
            "message_count": 0,
            "deleted_count": 0,
            "absent_count": 0,
            "failed_count": 0,
            "preserved_deletable_count": 0,
            "reactions_removed_count": 0,
            "reactions_absent_count": 0,
            "reactions_failed_count": 0,
            "preserved_reactions_count": 0,
            "foreign_reactions_normal_count": 0,
            "foreign_reactions_burst_count": 0,
            "foreign_reactions_unknown_count": 0,
        }
        decisions: Iterable[MessageDecision]
        if channel_plan is not None:
            decisions = channel_plan.decisions
        else:
            decisions = self._iter_message_decisions(
                messages=messages,
                cutoff_time=cutoff_time,
                delete_reactions=delete_reactions,
            )
        action_start = time.monotonic()
        for decision in decisions:
            stats["message_count"] += 1
            facts = decision.facts
            message = facts.message
            message_id = message["message_id"]
            message_time = facts.message_time

            if decision.preserve_message and facts.is_deletable:
                self.logger.debug(
                    "Preserving deletable message %s sent at %s UTC.",
                    sensitive(message_id),
                    format_timestamp(message_time),
                )
                stats["preserved_deletable_count"] += 1
                preserved_msg_ids.append(message_id)
                continue

            if decision.preserve_reaction_count > 0:
                stats["preserved_reactions_count"] += decision.preserve_reaction_count
                preserved_msg_ids.append(message_id)
                continue

            reaction_actions: List[PlannedAction] = []
            for action in decision.actions:
                if action.kind == ActionKind.DELETE_REACTION:
                    reaction_actions.append(action)
                    continue
                executed = self._execute_action(
                    action=action,
                    dry_run=dry_run,
                    facts=facts,
                )
                if action.kind == ActionKind.DELETE_MESSAGE:
                    if dry_run or executed == DeleteOutcome.DELETED:
                        stats["deleted_count"] += 1
                        self._accumulate_foreign_reaction_impact(
                            stats,
                            facts.foreign_reaction_impact,
                        )
                    elif executed == DeleteOutcome.ABSENT:
                        stats["absent_count"] += 1
                    else:
                        stats["failed_count"] += 1
            if reaction_actions:
                reaction_outcomes = self._execute_reaction_actions(
                    actions=reaction_actions,
                    dry_run=dry_run,
                )
                stats["reactions_removed_count"] += reaction_outcomes.deleted
                stats["reactions_absent_count"] += reaction_outcomes.absent
                stats["reactions_failed_count"] += reaction_outcomes.failed
        action_elapsed = time.monotonic() - action_start

        return preserved_msg_ids, stats, action_elapsed

    def clean_messages(
        self,
        dry_run: bool = False,
        fetch_sleep_time_range: Tuple[float, float] = (0.2, 0.4),
        delete_sleep_time_range: Tuple[float, float] = (1.5, 2),
        fetch_since: Optional[datetime] = None,
        max_messages: Union[int, float] = float("inf"),
        buffer_channel_messages: bool = False,
        delete_reactions: bool = False,
        delete_owned_threads: str = "none",
    ) -> int:
        """
        Run the cleaner across all eligible channels.

        Args:
            dry_run (bool): If True, no messages, reactions, or threads will be deleted.
            fetch_sleep_time_range (Tuple[float, float]): Minimum interval between fetch requests.
            delete_sleep_time_range (Tuple[float, float]): Minimum interval between delete requests.
            fetch_since (Optional[datetime]): Only fetch messages newer than this timestamp.
            max_messages (Union[int, float]): Maximum number of messages to fetch per channel.
            buffer_channel_messages (bool): If True, fully buffer one channel before evaluation.
            delete_reactions (bool): If True, remove the user's reactions on messages encountered.
            delete_owned_threads (str): Thread deletion mode: none, self-only, or all.

        Returns:
            int: Total number of messages deleted.
        """
        self._configure_request_policy(FETCH_POLICY, fetch_sleep_time_range)
        self._configure_request_policy(DELETE_POLICY, delete_sleep_time_range)
        if delete_owned_threads not in {"none", "self-only", "all"}:
            raise ValueError("delete_owned_threads must be 'none', 'self-only', or 'all'.")
        run_started_at = time.monotonic()
        total_stats = {
            "deleted_count": 0,
            "absent_count": 0,
            "failed_count": 0,
            "preserved_deletable_count": 0,
            "reactions_removed_count": 0,
            "reactions_absent_count": 0,
            "reactions_failed_count": 0,
            "preserved_reactions_count": 0,
            "threads_deleted_count": 0,
            "threads_absent_count": 0,
            "threads_failed_count": 0,
            "threads_planned_count": 0,
            "foreign_messages_affected_count": 0,
            "foreign_messages_unknown_count": 0,
            "foreign_reactions_normal_count": 0,
            "foreign_reactions_burst_count": 0,
            "foreign_reactions_unknown_count": 0,
        }

        cutoff_time = datetime.now(timezone.utc) - self.preserve_last
        if self.preserve_last > timedelta(0):
            self.logger.info("Deleting messages older than %s UTC.", format_timestamp(cutoff_time))
        if fetch_since:
            self.logger.info("Fetching messages not older than %s UTC.", format_timestamp(fetch_since))
        if delete_owned_threads != "none":
            thread_impact = (
                "self-only performs a complete author scan first, but can still remove other users' "
                "reactions"
                if delete_owned_threads == "self-only"
                else "all can remove messages and reactions from other users"
            )
            self.logger.warning(
                "Owned thread deletion enabled (%s): %s. Successful deletion removes the entire "
                "thread and overrides message/reaction retention settings for that thread.",
                delete_owned_threads,
                thread_impact,
            )

        if dry_run:
            self.logger.info(
                "Dry run enabled. Content will be fetched and evaluated but not deleted."
            )

        processed_channel_count = 0
        for channel in self.iter_channels():
            processed_channel_count += 1
            channel_started_at = time.monotonic()
            self.logger.progress("Processing channel: %s.", channel_str(channel))
            thread_outcome = self._prepare_owned_thread_deletion(
                channel=channel,
                mode=delete_owned_threads,
                dry_run=dry_run,
                fetch_sleep_time_range=fetch_sleep_time_range,
                fetch_since=fetch_since,
                max_messages=max_messages,
            )
            total_stats["threads_planned_count"] += int(thread_outcome.planned)
            total_stats["threads_deleted_count"] += int(thread_outcome.deleted)
            total_stats["threads_absent_count"] += int(thread_outcome.absent)
            total_stats["threads_failed_count"] += int(thread_outcome.failed)
            if thread_outcome.terminal:
                if thread_outcome.impact is not None and (
                    thread_outcome.planned or thread_outcome.deleted
                ):
                    if thread_outcome.impact.scan_complete:
                        total_stats["foreign_messages_affected_count"] += (
                            thread_outcome.impact.foreign_messages
                        )
                    else:
                        total_stats["foreign_messages_unknown_count"] += 1
                    self._accumulate_foreign_reaction_impact(
                        total_stats,
                        thread_outcome.impact.foreign_reactions,
                    )
                if self.preserve_cache:
                    self.preserve_cache.set_ids(channel_id=channel["id"], message_ids=[])
                    self.preserve_cache.save()
                continue

            force_buffer = thread_outcome.cleanup_messages is not None
            if force_buffer:
                messages: Iterable[DiscordMessage] = thread_outcome.cleanup_messages or []
                buffer_elapsed = thread_outcome.scan_elapsed
            else:
                messages, buffer_elapsed = self._prepare_channel_messages(
                    channel=channel,
                    fetch_sleep_time_range=fetch_sleep_time_range,
                    fetch_since=fetch_since,
                    max_messages=max_messages,
                    buffer_channel_messages=buffer_channel_messages,
                    dry_run=dry_run,
                )
            channel_delete_reactions = delete_reactions and not is_archived_thread(channel)
            if delete_reactions and not channel_delete_reactions:
                self.logger.info(
                    "Skipping reaction cleanup in archived thread %s; Discord only permits message deletion while archived.",
                    channel_str(channel),
                )
            channel_plan = None
            if buffer_channel_messages or force_buffer:
                channel_plan = self._build_channel_plan(
                    messages=messages,
                    cutoff_time=cutoff_time,
                    delete_reactions=channel_delete_reactions,
                )
                if not dry_run:
                    self._log_buffered_channel_pre_execution(
                        buffer_elapsed=buffer_elapsed or 0.0,
                        channel_plan=channel_plan,
                        delete_sleep_time_range=delete_sleep_time_range,
                    )
            preserved_msg_ids, stats, action_elapsed = self.delete_messages_older_than(
                messages=messages,
                cutoff_time=cutoff_time,
                delete_sleep_time_range=delete_sleep_time_range,
                dry_run=dry_run,
                channel_plan=channel_plan,
                delete_reactions=channel_delete_reactions,
            )
            if self.preserve_cache:
                self.preserve_cache.set_ids(channel_id=channel["id"], message_ids=preserved_msg_ids)
                self.preserve_cache.save()

            total_stats["deleted_count"] += stats["deleted_count"]
            total_stats["absent_count"] += stats.get("absent_count", 0)
            total_stats["failed_count"] += stats.get("failed_count", 0)
            total_stats["preserved_deletable_count"] += stats["preserved_deletable_count"]
            total_stats["reactions_removed_count"] += stats["reactions_removed_count"]
            total_stats["reactions_absent_count"] += stats.get(
                "reactions_absent_count",
                0,
            )
            total_stats["reactions_failed_count"] += stats.get(
                "reactions_failed_count",
                0,
            )
            total_stats["preserved_reactions_count"] += stats["preserved_reactions_count"]
            total_stats["foreign_reactions_normal_count"] += stats.get(
                "foreign_reactions_normal_count",
                0,
            )
            total_stats["foreign_reactions_burst_count"] += stats.get(
                "foreign_reactions_burst_count",
                0,
            )
            total_stats["foreign_reactions_unknown_count"] += stats.get(
                "foreign_reactions_unknown_count",
                0,
            )
            channel_elapsed = time.monotonic() - channel_started_at
            get_last_fetch_summary = getattr(self.api, "get_last_fetch_summary", None)
            fetch_summary = get_last_fetch_summary(channel["id"]) if callable(get_last_fetch_summary) else None
            if dry_run:
                channel_execute_estimate = self._format_duration(
                    self._estimate_action_count_duration(
                        stats["deleted_count"] + stats["reactions_removed_count"],
                        delete_sleep_time_range,
                    )
                )
                channel_total_estimate = self._format_duration(
                    channel_elapsed + self._estimate_action_count_duration(
                        stats["deleted_count"] + stats["reactions_removed_count"],
                        delete_sleep_time_range,
                    )
                )
                self._log_dry_run_channel_summary(
                    stats=stats,
                    fetch_summary=fetch_summary,
                    channel_elapsed=channel_elapsed,
                    channel_execute_estimate=channel_execute_estimate,
                    channel_total_estimate=channel_total_estimate,
                    delete_reactions=delete_reactions,
                    channel_plan=channel_plan,
                )
                continue

            self._log_executed_channel_summary(
                stats=stats,
                fetch_summary=fetch_summary,
                channel_elapsed=channel_elapsed,
                action_elapsed=action_elapsed,
                delete_reactions=delete_reactions,
                channel_plan=channel_plan,
            )

        self.logger.progress("Channels processed: %s.", processed_channel_count)
        run_elapsed = time.monotonic() - run_started_at
        if dry_run:
            execute_estimate_seconds = self._estimate_action_count_duration(
                total_stats["deleted_count"]
                + total_stats["reactions_removed_count"]
                + total_stats["threads_planned_count"],
                delete_sleep_time_range,
            )
            total_summary = (
                f"Summary: messages {total_stats['deleted_count']} delete / "
                f"{total_stats['preserved_deletable_count']} keep"
            )
            if delete_reactions:
                total_summary += (
                    f", reactions {total_stats['reactions_removed_count']} delete / "
                    f"{total_stats['preserved_reactions_count']} keep"
                )
            if delete_owned_threads != "none":
                total_summary += f", owned threads {total_stats['threads_planned_count']} delete"
            if total_stats["threads_planned_count"]:
                if total_stats["foreign_messages_unknown_count"]:
                    total_summary += ", foreign messages affected unknown"
                else:
                    total_summary += (
                        ", foreign messages affected "
                        f"{total_stats['foreign_messages_affected_count']}"
                    )
            if total_stats["deleted_count"] or total_stats["threads_planned_count"]:
                total_summary += (
                    ", foreign reactions affected "
                    f"{self._format_foreign_reaction_stats(total_stats)}"
                )
            self.logger.info(total_summary)
            self.logger.info(
                "scan time=%s, est. execute time=%s, est. total time=%s",
                self._format_duration(run_elapsed),
                self._format_duration(execute_estimate_seconds),
                self._format_duration(run_elapsed + execute_estimate_seconds),
            )
        else:
            total_summary = (
                f"Summary: messages {total_stats['deleted_count']} deleted / "
                f"{total_stats['absent_count']} absent / "
                f"{total_stats['failed_count']} failed / "
                f"{total_stats['preserved_deletable_count']} kept"
            )
            if delete_reactions:
                total_summary += (
                    f", reactions {total_stats['reactions_removed_count']} deleted / "
                    f"{total_stats['reactions_absent_count']} absent / "
                    f"{total_stats['reactions_failed_count']} failed / "
                    f"{total_stats['preserved_reactions_count']} kept"
                )
            if delete_owned_threads != "none":
                total_summary += (
                    f", owned threads {total_stats['threads_deleted_count']} deleted / "
                    f"{total_stats['threads_absent_count']} absent / "
                    f"{total_stats['threads_failed_count']} failed"
                )
            self.logger.info(total_summary)

        return total_stats["deleted_count"]

    def _prepare_owned_thread_deletion(
        self,
        channel: DiscordChannel,
        mode: str,
        dry_run: bool,
        fetch_sleep_time_range: Tuple[float, float],
        fetch_since: Optional[datetime],
        max_messages: Union[int, float],
    ) -> _OwnedThreadDeletionOutcome:
        """Plan or execute deletion of one creator-owned thread, with a safe fallback."""
        if mode == "none" or not is_thread_channel(channel.get("type")):
            return _OwnedThreadDeletionOutcome()

        owner_id = channel.get("owner_id")
        if owner_id is None:
            self.logger.diagnostic(
                "Skipping owned thread deletion for %s because Discord omitted owner_id.",
                channel_str(channel),
            )
            return _OwnedThreadDeletionOutcome()
        if str(owner_id) != self.user_id:
            self.logger.diagnostic(
                "Skipping owned thread deletion for %s because it was created by another user.",
                channel_str(channel),
            )
            return _OwnedThreadDeletionOutcome()

        if mode == "all" and not dry_run:
            return self._attempt_owned_thread_deletion(
                channel=channel,
                mode=mode,
                dry_run=dry_run,
            )

        scan_started_at = time.monotonic()
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
        scanned_messages = list(
            self.fetch_all_messages(
                channel=channel,
                fetch_sleep_time_range=fetch_sleep_time_range,
                fetch_since=None,
                max_messages=float("inf"),
            )
        )
        scan_elapsed = time.monotonic() - scan_started_at

        get_last_fetch_summary = getattr(self.api, "get_last_fetch_summary", None)
        fetch_summary = get_last_fetch_summary(channel["id"]) if callable(get_last_fetch_summary) else None
        message_count = channel.get("message_count")
        scan_complete = (
            isinstance(fetch_summary, dict)
            and fetch_summary.get("complete") is True
            and isinstance(message_count, int)
            and not isinstance(message_count, bool)
            and message_count >= 0
            and len(scanned_messages) >= message_count
        )
        impact = self._build_thread_deletion_impact(
            messages=scanned_messages,
            scan_complete=scan_complete,
        )
        self._log_thread_deletion_impact(channel=channel, impact=impact)

        if mode == "all":
            return self._attempt_owned_thread_deletion(
                channel=channel,
                mode=mode,
                dry_run=dry_run,
                impact=impact,
            )

        cleanup_messages = self._messages_from_complete_thread_scan(
            channel=channel,
            messages=scanned_messages,
            fetch_since=fetch_since,
            max_messages=max_messages,
        )
        if not scan_complete:
            self.logger.warning(
                "Skipping self-only thread deletion for %s because a complete history scan could not be proven.",
                channel_str(channel),
            )
            return _OwnedThreadDeletionOutcome(
                cleanup_messages=cleanup_messages,
                scan_elapsed=scan_elapsed,
            )

        foreign_message_count = impact.foreign_messages
        if foreign_message_count:
            self.logger.info(
                "Skipping self-only thread deletion for %s because it contains %s message(s) "
                "from other or unknown authors.",
                channel_str(channel),
                foreign_message_count,
            )
            return _OwnedThreadDeletionOutcome(
                cleanup_messages=cleanup_messages,
                scan_elapsed=scan_elapsed,
            )

        outcome = self._attempt_owned_thread_deletion(
            channel=channel,
            mode=mode,
            dry_run=dry_run,
            impact=impact,
        )
        if outcome.terminal:
            return outcome
        return _OwnedThreadDeletionOutcome(
            failed=outcome.failed,
            cleanup_messages=cleanup_messages,
            scan_elapsed=scan_elapsed,
        )

    def _attempt_owned_thread_deletion(
        self,
        channel: DiscordChannel,
        mode: str,
        dry_run: bool,
        impact: Optional[_ThreadDeletionImpact] = None,
    ) -> _OwnedThreadDeletionOutcome:
        impact_description = (
            "including messages and reactions from other users"
            if mode == "all"
            else "after finding no messages from other authors in the completed scan"
        )
        if dry_run:
            self.logger.event(
                "Would delete owned thread %s, %s.",
                channel_str(channel),
                impact_description,
                indent=1,
                prefix="-",
            )
            return _OwnedThreadDeletionOutcome(
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
        )
        delete_outcome = self.api.delete_thread(channel["id"])
        if delete_outcome == DeleteOutcome.DELETED:
            return _OwnedThreadDeletionOutcome(
                terminal=True,
                deleted=True,
                impact=impact,
            )
        if delete_outcome == DeleteOutcome.ABSENT:
            return _OwnedThreadDeletionOutcome(
                terminal=True,
                absent=True,
            )

        self.logger.warning(
            "Owned thread deletion failed for %s; falling back to ordinary message and reaction cleanup.",
            channel_str(channel),
        )
        return _OwnedThreadDeletionOutcome(failed=True)

    def _build_thread_deletion_impact(
        self,
        messages: List[DiscordMessage],
        scan_complete: bool,
    ) -> _ThreadDeletionImpact:
        """Summarize first-class messages and dependent foreign reactions."""
        own_messages = sum(
            message.get("author_id") == self.user_id for message in messages
        )
        foreign_reactions = ForeignReactionImpact()
        for message in messages:
            foreign_reactions = foreign_reactions.combined_with(
                self._foreign_reaction_impact(message.get("reactions") or [])
            )
        if not scan_complete:
            foreign_reactions = ForeignReactionImpact(
                normal=foreign_reactions.normal,
                burst=foreign_reactions.burst,
                complete=False,
            )
        return _ThreadDeletionImpact(
            own_messages=own_messages,
            foreign_messages=len(messages) - own_messages,
            foreign_reactions=foreign_reactions,
            scan_complete=scan_complete,
        )

    def _log_thread_deletion_impact(
        self,
        channel: DiscordChannel,
        impact: _ThreadDeletionImpact,
    ) -> None:
        """Report the exact deletion cascade when the completed scan permits it."""
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
            self._format_foreign_reaction_impact(impact.foreign_reactions),
            indent=1,
            prefix="-",
        )

    def _messages_from_complete_thread_scan(
        self,
        channel: DiscordChannel,
        messages: List[DiscordMessage],
        fetch_since: Optional[datetime],
        max_messages: Union[int, float],
    ) -> List[DiscordMessage]:
        """Apply normal cleanup fetch boundaries to an already complete thread scan."""
        selected: List[DiscordMessage] = []
        for message in messages:
            message_time = datetime.fromisoformat(message["timestamp"].replace('Z', '+00:00'))
            if fetch_since and message_time < fetch_since:
                break
            if len(selected) >= max_messages:
                break
            selected.append(message)

        message_stream: Iterable[DiscordMessage] = iter(selected)
        if self.preserve_cache:
            cached_ids = self.preserve_cache.get_ids(channel_id=channel["id"])
            message_stream = self._merge_cached_messages(
                channel=channel,
                main_messages=message_stream,
                cached_ids=cached_ids,
            )
        return list(message_stream)

    def _prepare_channel_messages(
        self,
        channel: DiscordChannel,
        fetch_sleep_time_range: Tuple[float, float],
        fetch_since: Optional[datetime],
        max_messages: Union[int, float],
        buffer_channel_messages: bool,
        dry_run: bool = False,
    ) -> Tuple[Iterable[DiscordMessage], Optional[float]]:
        """Prepare one channel's message stream, optionally buffering it fully first."""
        messages: Iterable[DiscordMessage] = self.fetch_all_messages(
            channel=channel,
            fetch_sleep_time_range=fetch_sleep_time_range,
            fetch_since=fetch_since,
            max_messages=max_messages,
        )
        if self.preserve_cache:
            cached_ids = self.preserve_cache.get_ids(channel_id=channel["id"])
            self.logger.debug(
                "Merging %s cached preserved message IDs for channel %s.",
                len(cached_ids), channel_str(channel)
            )
            messages = self._merge_cached_messages(
                channel=channel,
                main_messages=messages,
                cached_ids=cached_ids
            )
        if buffer_channel_messages:
            buffered_messages, buffer_elapsed = self._buffer_channel_messages(messages=messages)
            return buffered_messages, buffer_elapsed
        return messages, None

    def _buffer_channel_messages(
        self,
        messages: Iterable[DiscordMessage],
    ) -> Tuple[List[DiscordMessage], float]:
        """Buffer one channel into memory and return the buffered messages plus elapsed time."""
        started_at = time.monotonic()
        return list(messages), time.monotonic() - started_at

    def _build_channel_plan(
        self,
        messages: Iterable[DiscordMessage],
        cutoff_time: datetime,
        delete_reactions: bool,
    ) -> ChannelPlan:
        """Build a full per-channel plan from buffered messages."""
        return ChannelPlan(
            decisions=tuple(
                self._iter_message_decisions(
                    messages=messages,
                    cutoff_time=cutoff_time,
                    delete_reactions=delete_reactions,
                )
            )
        )

    def _iter_message_decisions(
        self,
        messages: Iterable[DiscordMessage],
        cutoff_time: datetime,
        delete_reactions: bool,
    ) -> Iterable[MessageDecision]:
        """Yield one decision per message in newest-to-oldest order."""
        preserve_n_count = 0
        preserve_count_active = self.preserve_n > 0

        for message in messages:
            facts = self._build_message_facts(message=message, delete_reactions=delete_reactions)
            if preserve_count_active and (self.preserve_n_mode == "all" or facts.is_deletable):
                preserve_n_count += 1

            in_preserve_n_count_window = preserve_count_active and preserve_n_count <= self.preserve_n
            in_preserve_window = in_preserve_n_count_window or facts.message_time >= cutoff_time
            yield self._build_message_decision(
                facts=facts,
                in_preserve_window=in_preserve_window,
            )

    def _build_message_facts(
        self,
        message: DiscordMessage,
        delete_reactions: bool,
    ) -> MessageFacts:
        """Derive normalized facts from one fetched message."""
        message_time = datetime.fromisoformat(message["timestamp"].replace('Z', '+00:00'))
        is_author = message["author_id"] == self.user_id
        is_deletable = is_author and bool(getattr(message["type"], "deletable", False))
        reactions = message.get("reactions") or []
        my_reactions: list[OwnedReaction] = []
        if delete_reactions:
            for reaction in reactions:
                emoji = reaction.get("emoji") or {}
                if reaction.get("me"):
                    my_reactions.append(
                        OwnedReaction(emoji=emoji, reaction_type=ReactionType.NORMAL)
                    )
                if reaction.get("me_burst"):
                    my_reactions.append(
                        OwnedReaction(emoji=emoji, reaction_type=ReactionType.BURST)
                    )
        return MessageFacts(
            message=message,
            message_time=message_time,
            is_author=is_author,
            is_deletable=is_deletable,
            my_reactions=tuple(my_reactions),
            foreign_reaction_impact=self._foreign_reaction_impact(reactions),
        )

    @staticmethod
    def _foreign_reaction_impact(
        reactions: Iterable[DiscordReaction],
    ) -> ForeignReactionImpact:
        """Derive exact foreign reaction instances from Discord count details."""
        impact = ForeignReactionImpact()
        for reaction in reactions:
            details = reaction.get("count_details")
            me = reaction.get("me")
            me_burst = reaction.get("me_burst")
            if (
                not isinstance(details, dict)
                or not isinstance(me, bool)
                or not isinstance(me_burst, bool)
            ):
                impact = impact.combined_with(ForeignReactionImpact(complete=False))
                continue

            normal = details.get("normal")
            burst = details.get("burst")
            if (
                not isinstance(normal, int)
                or isinstance(normal, bool)
                or normal < int(me)
                or not isinstance(burst, int)
                or isinstance(burst, bool)
                or burst < int(me_burst)
            ):
                impact = impact.combined_with(ForeignReactionImpact(complete=False))
                continue

            total = reaction.get("count")
            if (
                not isinstance(total, int)
                or isinstance(total, bool)
                or total != normal + burst
            ):
                impact = impact.combined_with(ForeignReactionImpact(complete=False))
                continue

            impact = impact.combined_with(
                ForeignReactionImpact(
                    normal=normal - int(me),
                    burst=burst - int(me_burst),
                )
            )
        return impact

    def _build_message_decision(
        self,
        facts: MessageFacts,
        in_preserve_window: bool,
    ) -> MessageDecision:
        """Convert message facts plus preserve-window state into a decision and actions."""
        actions: List[PlannedAction] = []
        preserve_message = in_preserve_window and facts.is_deletable
        preserve_reactions = in_preserve_window and (not facts.is_deletable) and bool(facts.my_reactions)

        if not in_preserve_window:
            if facts.is_deletable:
                actions.append(
                    PlannedAction(
                        kind=ActionKind.DELETE_MESSAGE,
                        channel_id=facts.message["channel_id"],
                        message_id=facts.message["message_id"],
                        message_time=facts.message_time,
                    )
                )
            else:
                for reaction in facts.my_reactions:
                    actions.append(
                        PlannedAction(
                            kind=ActionKind.DELETE_REACTION,
                            channel_id=facts.message["channel_id"],
                            message_id=facts.message["message_id"],
                            message_time=facts.message_time,
                            emoji=reaction.emoji,
                            reaction_type=reaction.reaction_type,
                        )
                    )

        return MessageDecision(
            facts=facts,
            preserve_message=preserve_message,
            preserve_reactions=preserve_reactions,
            actions=tuple(actions),
        )

    def _log_buffered_channel_pre_execution(
        self,
        buffer_elapsed: float,
        channel_plan: ChannelPlan,
        delete_sleep_time_range: Tuple[float, float],
    ) -> None:
        """Log one concise pre-execution summary line for buffered real runs."""
        self.logger.progress(
            "Buffered messages=%s, scan time=%s, est. execute time=%s.",
            channel_plan.buffered_message_count,
            self._format_duration(buffer_elapsed),
            self._format_duration(self._estimate_action_duration(channel_plan, delete_sleep_time_range)),
            indent=1,
            prefix="-",
        )

    def _estimate_action_duration(
        self,
        channel_plan: ChannelPlan,
        delete_sleep_time_range: Tuple[float, float],
    ) -> float:
        """Estimate execution time from planned action count and configured sleep range."""
        return self._estimate_action_count_duration(channel_plan.action_count, delete_sleep_time_range)

    def _estimate_action_count_duration(
        self,
        action_count: int,
        delete_sleep_time_range: Tuple[float, float],
    ) -> float:
        """Estimate pacing time between a sequence of planned actions."""
        average_sleep = sum(delete_sleep_time_range) / 2
        return max(0, action_count - 1) * average_sleep

    def _format_duration(self, seconds: float) -> str:
        """Format a duration in seconds as HH:MM:SS."""
        whole_seconds = max(0, int(round(seconds)))
        hours, remainder = divmod(whole_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    @staticmethod
    def _accumulate_foreign_reaction_impact(
        stats: dict[str, int],
        impact: ForeignReactionImpact,
    ) -> None:
        """Add one exact-or-unknown impact result to a statistics mapping."""
        stats["foreign_reactions_normal_count"] += impact.normal
        stats["foreign_reactions_burst_count"] += impact.burst
        stats["foreign_reactions_unknown_count"] += int(not impact.complete)

    @staticmethod
    def _format_foreign_reaction_impact(impact: ForeignReactionImpact) -> str:
        """Render an exact normal/Super split or an explicit unknown result."""
        if not impact.complete:
            return "unknown"
        return f"{impact.normal} normal / {impact.burst} super"

    def _format_foreign_reaction_stats(self, stats: dict[str, int]) -> str:
        """Render aggregate impact statistics using the same exactness contract."""
        return self._format_foreign_reaction_impact(
            ForeignReactionImpact(
                normal=stats["foreign_reactions_normal_count"],
                burst=stats["foreign_reactions_burst_count"],
                complete=stats["foreign_reactions_unknown_count"] == 0,
            )
        )

    def _log_fetch_summary(self, fetch_summary: Optional[dict]) -> None:
        """Log per-channel fetch diagnostics in a compact, consistent block."""
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

    def _format_channel_summary(
        self,
        stats: dict[str, int],
        delete_reactions: bool,
        dry_run: bool,
        channel_plan: Optional[ChannelPlan] = None,
    ) -> str:
        """Build the human-facing per-channel summary line."""
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
            summary += f", buffered messages={channel_plan.buffered_message_count}"
        if dry_run and stats["deleted_count"]:
            summary += (
                ", foreign reactions affected "
                f"{self._format_foreign_reaction_stats(stats)}"
            )
        return summary

    def _log_dry_run_channel_summary(
        self,
        stats: dict[str, int],
        fetch_summary: Optional[dict],
        channel_elapsed: float,
        channel_execute_estimate: str,
        channel_total_estimate: str,
        delete_reactions: bool,
        channel_plan: Optional[ChannelPlan] = None,
    ) -> None:
        """Log dry-run output for one channel."""
        self._log_fetch_summary(fetch_summary)
        self.logger.progress(
            self._format_channel_summary(
                stats=stats,
                delete_reactions=delete_reactions,
                dry_run=True,
                channel_plan=channel_plan,
            ),
            indent=1,
            prefix="-",
        )
        self.logger.progress(
            "scan time=%s, est. execute time=%s, est. total time=%s",
            self._format_duration(channel_elapsed),
            channel_execute_estimate,
            channel_total_estimate,
            indent=2,
        )

    def _log_executed_channel_summary(
        self,
        stats: dict[str, int],
        fetch_summary: Optional[dict],
        channel_elapsed: float,
        action_elapsed: float,
        delete_reactions: bool,
        channel_plan: Optional[ChannelPlan] = None,
    ) -> None:
        """Log executed-run output for one channel."""
        self._log_fetch_summary(fetch_summary)
        self.logger.progress(
            self._format_channel_summary(
                stats=stats,
                delete_reactions=delete_reactions,
                dry_run=False,
                channel_plan=channel_plan,
            ),
            indent=1,
            prefix="-",
        )
        if channel_plan is not None:
            self.logger.progress(
                "execute time=%s, total time=%s",
                self._format_duration(action_elapsed),
                self._format_duration(channel_elapsed),
                indent=2,
            )
        else:
            self.logger.progress("total time=%s", self._format_duration(channel_elapsed), indent=2)

    def _execute_action(
        self,
        action: PlannedAction,
        dry_run: bool,
        facts: Optional[MessageFacts] = None,
    ) -> Optional[DeleteOutcome]:
        """Execute a single planned action or simulate it in dry-run mode."""
        if action.kind == ActionKind.DELETE_MESSAGE:
            if dry_run:
                self.logger.event(
                    "Would delete message %s.",
                    sensitive(action.message_id),
                    indent=1,
                    prefix="-",
                )
                self._log_message_detail(facts)
                return None

            self.logger.event(
                "Deleting message %s.",
                sensitive(action.message_id),
                indent=1,
                prefix="-",
            )
            self._log_message_detail(facts)
            outcome = self.api.delete_message(
                channel_id=action.channel_id,
                message_id=action.message_id,
            )
            if outcome == DeleteOutcome.FAILED:
                self.logger.warning(
                    "Failed to delete message %s in channel %s.",
                    sensitive(action.message_id),
                    sensitive(action.channel_id),
                )
            return outcome

        emoji: DiscordEmoji = action.emoji or {}
        emoji_name = emoji.get("name") or "unknown"
        reaction_label = self._reaction_action_label(action)
        if dry_run:
            self.logger.event(
                "Would delete %s from message %s.",
                reaction_label,
                sensitive(action.message_id),
                indent=1,
                prefix="-",
            )
            self._log_reaction_detail(emoji_name)
            return None

        self.logger.event(
            "Deleting %s from message %s.",
            reaction_label,
            sensitive(action.message_id),
            indent=1,
            prefix="-",
        )
        self._log_reaction_detail(emoji_name)
        outcome = self.api.delete_own_reaction(
            channel_id=action.channel_id,
            message_id=action.message_id,
            emoji=emoji,
            reaction_type=action.reaction_type,
        )
        if outcome == DeleteOutcome.FAILED:
            self.logger.warning(
                "Failed to delete reaction %s on message %s in channel %s.",
                sensitive(emoji_name, full=True),
                sensitive(action.message_id),
                sensitive(action.channel_id),
            )
        return outcome

    def _execute_reaction_actions(
        self,
        actions: List[PlannedAction],
        dry_run: bool,
    ) -> _DeleteActionCounts:
        """Execute one or more reaction deletions for the same message as one visible event block."""
        if not actions:
            return _DeleteActionCounts()

        message_id = actions[0].message_id
        emoji_names = [self._reaction_detail(action) for action in actions]
        reaction_count = len(actions)
        reaction_label = (
            self._reaction_action_label(actions[0])
            if reaction_count == 1
            else f"{reaction_count} reactions"
        )

        if dry_run:
            self.logger.event(
                "Would delete %s from message %s.",
                reaction_label,
                sensitive(message_id),
                indent=1,
                prefix="-",
            )
            self._log_reaction_detail(emoji_names)
            return _DeleteActionCounts(deleted=reaction_count)

        self.logger.event(
            "Deleting %s from message %s.",
            reaction_label,
            sensitive(message_id),
            indent=1,
            prefix="-",
        )
        self._log_reaction_detail(emoji_names)

        outcomes = _DeleteActionCounts()
        for action in actions:
            emoji: DiscordEmoji = action.emoji or {}
            emoji_name = emoji.get("name") or "unknown"
            outcome = self.api.delete_own_reaction(
                channel_id=action.channel_id,
                message_id=action.message_id,
                emoji=emoji,
                reaction_type=action.reaction_type,
            )
            outcomes.record(outcome)
            if outcome == DeleteOutcome.FAILED:
                self.logger.warning(
                    "Failed to delete reaction %s on message %s in channel %s.",
                    sensitive(emoji_name, full=True),
                    sensitive(action.message_id),
                    sensitive(action.channel_id),
                )

        return outcomes

    @staticmethod
    def _reaction_action_label(action: PlannedAction) -> str:
        return "Super Reaction" if action.reaction_type == ReactionType.BURST else "reaction"

    @staticmethod
    def _reaction_detail(action: PlannedAction) -> str:
        name = (action.emoji or {}).get("name") or "unknown"
        return f"{name} (super)" if action.reaction_type == ReactionType.BURST else name

    def _log_message_detail(self, facts: Optional[MessageFacts]) -> None:
        if not facts:
            return
        content = (facts.message.get("content") or "").strip()
        if content:
            normalized = " ".join(content.split())
            if len(normalized) > 120:
                normalized = f"{normalized[:117]}..."
            self.logger.detail("Content: %s", sensitive(normalized, full=True), indent=2, no_wrap=True)

    def _log_reaction_detail(self, emoji_names: Union[str, List[str]]) -> None:
        if isinstance(emoji_names, str):
            names = [emoji_names]
        else:
            names = emoji_names
        rendered = ", ".join(str(sensitive(name, full=True)) for name in names)
        label = "Reaction" if len(names) == 1 else "Reactions"
        self.logger.detail(f"{label}: %s", rendered, indent=2, no_wrap=True)

    def _merge_cached_messages(
        self,
        channel: DiscordChannel,
        main_messages: Iterable[DiscordMessage],
        cached_ids: List[str],
    ) -> Generator[DiscordMessage, None, None]:
        """
        Merge the main message stream with cached IDs while preserving descending
        snowflake order and avoiding duplicate processing.
        """

        if cached_ids and int(cached_ids[0]) < int(cached_ids[-1]):
            raise ValueError("Cached message IDs must be in descending order (newest first).")
        cache: List[Tuple[int, str]] = [(int(mid), mid) for mid in cached_ids]
        cache_idx = 0
        seen_ids: Set[str] = set()

        def emit_cache_until(main_id_int: int) -> Generator[DiscordMessage, None, None]:
            nonlocal cache_idx
            while cache_idx < len(cache) and cache[cache_idx][0] > main_id_int:
                _, mid = cache[cache_idx]
                if mid not in seen_ids:
                    msg = self.api.fetch_message_by_id(channel_id=channel["id"], message_id=mid)
                    if msg:
                        seen_ids.add(mid)
                        yield msg
                cache_idx += 1

        for main_msg in main_messages:
            mid = main_msg["message_id"]
            mid_int = int(mid)
            for cached in emit_cache_until(mid_int):
                yield cached
            if mid not in seen_ids:
                seen_ids.add(mid)
                yield main_msg

        # Emit any remaining cached items (older than the last main message).
        for cached in emit_cache_until(-1):
            yield cached
