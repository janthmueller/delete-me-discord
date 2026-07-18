"""Cleanup run service and channel orchestration."""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Generator, Iterable, List, Optional, Set, Tuple, Union

from ..discord.channel_types import is_archived_thread
from ..discord.client import DiscordClient
from ..discord.models import DiscordChannel, DiscordMessage
from ..discord.rate_limits import DELETE_POLICY, FETCH_POLICY
from ..scope import (
    CleanupChannelContext,
    ScopeFilter,
    ScopeDiscoverySeed,
    ScopeInventory,
    ScopeRules,
    iter_cleanup_channel_contexts,
    should_include_channel,
)
from .preserve_cache import PreserveCache
from .thread_deletion import OwnedThreadDeletionCoordinator
from .threads import (
    ARCHIVED_THREAD_CLEANUP_MODES,
    ArchivedThreadAssessment,
    ArchivedThreadCoordinator,
    ThreadRestorationJournal,
    ThreadRestoreOutcome,
)
from ..utils import channel_str, format_timestamp
from .executor import ChannelExecutor
from .models import (
    ActionKind,
    ChannelCleanupStats,
    ChannelPlan,
    CleanupRunOptions,
    CleanupRunStats,
)
from .planner import CleanupPlanner, CleanupPolicy
from .reporting import CleanupReporter

class MessageCleaner:
    def __init__(
        self,
        api: DiscordClient,
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
        thread_restoration_journal: Optional[ThreadRestorationJournal] = None,
    ):
        """
        Initializes the MessageCleaner.

        Args:
            api (DiscordClient): An instance of DiscordClient.
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
        resolved_user_id = user_id or os.getenv("DISCORD_USER_ID")
        if not resolved_user_id:
            try:
                current_user = self.api.get_current_user()
                resolved_user_id = current_user.get("id")
            except Exception:
                resolved_user_id = None
        if not isinstance(resolved_user_id, str) or not resolved_user_id:
            raise ValueError("User ID not provided. Set DISCORD_USER_ID or ensure the token can fetch /users/@me.")
        self.user_id = resolved_user_id

        self.scope_rules = ScopeRules.from_values(include_ids, exclude_ids)
        self.preserve_last = preserve_last
        self.preserve_n = preserve_n
        if preserve_n_mode not in {"mine", "all"}:
            raise ValueError("preserve_n_mode must be 'mine' or 'all'.")
        self.preserve_n_mode = preserve_n_mode
        self.logger: Any = logging.getLogger(self.__class__.__name__)
        self.reporter = CleanupReporter(self.logger)
        self.preserve_cache = preserve_cache
        self.scope_inventory = scope_inventory
        self.scope_seed = scope_seed
        self.scope_filter = scope_filter or (
            scope_inventory.scope_filter if scope_inventory else ScopeFilter()
        )
        self.thread_restoration_journal = thread_restoration_journal
        self._thread_state_clock = time.monotonic
        self._current_channel_context: CleanupChannelContext | None = None

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
        try:
            for context in self.iter_channel_contexts():
                self._current_channel_context = context
                yield context.channel
                self._current_channel_context = None
        finally:
            self._current_channel_context = None

    def iter_channel_contexts(self) -> Generator[CleanupChannelContext, None, None]:
        """Yield eligible channels with guild and parent permission context."""
        if self.scope_inventory is None:
            target_label = (
                "channels and threads"
                if self.scope_filter.thread_discovery_mode != "none"
                else "channels"
            )
            self.logger.progress("Discovering %s as cleanup advances.", target_label)
        for context in iter_cleanup_channel_contexts(
            self.api,
            scope_filter=self.scope_filter,
            inventory=self.scope_inventory,
            seed=self.scope_seed,
        ):
            channel = context.channel
            if not self._should_include_channel(channel):
                continue
            self.logger.debug("Included channel: %s.", channel_str(channel))
            yield context

    def _should_include_channel(self, channel: DiscordChannel) -> bool:
        """
        Determines if a channel should be included based on include and exclude IDs.

        Args:
            channel (DiscordChannel): The channel payload.

        Returns:
            bool: True if the channel should be included, False otherwise.
        """
        allowed = should_include_channel(
            channel,
            self.scope_rules,
            self.scope_filter,
        )
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
        resume_archived_thread: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[str], ChannelCleanupStats, float]:
        """
        Execute planned actions for one channel and collect per-channel stats.

        Args:
            messages (Iterable[DiscordMessage]): Message data in newest-to-oldest order.
            cutoff_time (datetime): The cutoff datetime; messages older than this will be deleted.
            delete_sleep_time_range (Tuple[float, float]): Minimum interval between delete requests.
            dry_run (bool): If True, simulate deletions without calling the API.
            delete_reactions (bool): If True, remove the user's reactions on messages encountered.
            channel_plan (Optional[ChannelPlan]): Optional precomputed channel plan.
            resume_archived_thread (Optional[Callable[[], bool]]): Reopen a thread
                after Discord reports it archived, when the coordinator permits it.

        Returns:
            Tuple[List[str], ChannelCleanupStats, float]: Preserved message IDs,
                statistics, and action-phase elapsed time.
        """
        result = ChannelExecutor(
            api=self.api,
            logger=self.logger,
            configure_request_policy=self._configure_request_policy,
        ).execute(
            messages=messages,
            planner=self._planner(
                cutoff_time=cutoff_time,
                delete_reactions=delete_reactions,
            ),
            delete_sleep_time_range=delete_sleep_time_range,
            dry_run=dry_run,
            channel_plan=channel_plan,
            resume_archived_thread=resume_archived_thread,
        )
        return result.as_legacy_tuple()

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
        archived_thread_cleanup: str = "skip",
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
            archived_thread_cleanup (str): Archived content mode: skip, temporary, or allow-active.

        Returns:
            int: Total number of messages deleted.
        """
        self._configure_request_policy(FETCH_POLICY, fetch_sleep_time_range)
        self._configure_request_policy(DELETE_POLICY, delete_sleep_time_range)
        if delete_owned_threads not in {"none", "self-only", "all"}:
            raise ValueError("delete_owned_threads must be 'none', 'self-only', or 'all'.")
        if archived_thread_cleanup not in ARCHIVED_THREAD_CLEANUP_MODES:
            raise ValueError(
                "archived_thread_cleanup must be 'skip', 'temporary', or 'allow-active'."
            )
        archived_thread_coordinator = ArchivedThreadCoordinator(
            api=self.api,
            user_id=self.user_id,
            journal=self.thread_restoration_journal,
            logger=self.logger,
            clock=self._thread_state_clock,
        )
        owned_thread_deletion_coordinator = OwnedThreadDeletionCoordinator(
            api=self.api,
            user_id=self.user_id,
            logger=self.logger,
            report_impact=self.reporter.log_thread_deletion_impact,
        )
        if not dry_run:
            recovered_count, recovery_failed_count = (
                archived_thread_coordinator.restore_pending()
            )
            if recovered_count:
                self.logger.info(
                    "Restored %s thread(s) left active by an interrupted cleanup.",
                    recovered_count,
                )
            if recovery_failed_count:
                self.logger.error(
                    "%s interrupted thread restoration(s) remain unresolved.",
                    recovery_failed_count,
                )
        run_started_at = time.monotonic()
        total_stats = CleanupRunStats()

        cutoff_time = datetime.now(timezone.utc) - self.preserve_last
        options = CleanupRunOptions(
            cutoff_time=cutoff_time,
            dry_run=dry_run,
            fetch_sleep_time_range=fetch_sleep_time_range,
            delete_sleep_time_range=delete_sleep_time_range,
            fetch_since=fetch_since,
            max_messages=max_messages,
            buffer_channel_messages=buffer_channel_messages,
            delete_reactions=delete_reactions,
            delete_owned_threads=delete_owned_threads,
            archived_thread_cleanup=archived_thread_cleanup,
        )
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
            context = self._current_channel_context
            if context is None or context.channel is not channel:
                context = CleanupChannelContext(channel=channel)
            processed_channel_count += 1
            total_stats.merge(
                self._process_channel(
                    channel=channel,
                    context=context,
                    options=options,
                    archived_thread_coordinator=archived_thread_coordinator,
                    owned_thread_deletion_coordinator=(
                        owned_thread_deletion_coordinator
                    ),
                )
            )
        self.logger.progress("Channels processed: %s.", processed_channel_count)
        run_elapsed = time.monotonic() - run_started_at
        self.reporter.log_run_summary(
            stats=total_stats,
            options=options,
            run_elapsed=run_elapsed,
        )
        return total_stats.deleted_count

    def _process_channel(
        self,
        channel: DiscordChannel,
        context: CleanupChannelContext,
        options: CleanupRunOptions,
        archived_thread_coordinator: ArchivedThreadCoordinator,
        owned_thread_deletion_coordinator: OwnedThreadDeletionCoordinator,
    ) -> CleanupRunStats:
        """Process one channel as an isolated cleanup transaction."""
        run_stats = CleanupRunStats()
        channel_started_at = time.monotonic()
        self.logger.progress("Processing channel: %s.", channel_str(channel))
        thread_outcome = owned_thread_deletion_coordinator.prepare(
            channel=channel,
            mode=options.delete_owned_threads,
            dry_run=options.dry_run,
            fetch_complete_history=lambda: list(
                self.fetch_all_messages(
                    channel=channel,
                    fetch_sleep_time_range=options.fetch_sleep_time_range,
                    fetch_since=None,
                    max_messages=float("inf"),
                )
            ),
        )
        run_stats.threads_planned_count += int(thread_outcome.planned)
        run_stats.threads_deleted_count += int(thread_outcome.deleted)
        run_stats.threads_absent_count += int(thread_outcome.absent)
        run_stats.threads_failed_count += int(thread_outcome.failed)
        if thread_outcome.terminal:
            if thread_outcome.impact is not None and (
                thread_outcome.planned or thread_outcome.deleted
            ):
                if thread_outcome.impact.scan_complete:
                    run_stats.foreign_messages_affected_count += (
                        thread_outcome.impact.foreign_messages
                    )
                else:
                    run_stats.foreign_messages_unknown_count += 1
                run_stats.add_foreign_reaction_impact(
                    thread_outcome.impact.foreign_reactions,
                )
            if self.preserve_cache:
                self.preserve_cache.set_ids(
                    channel_id=channel["id"],
                    message_ids=[],
                )
                self.preserve_cache.save()
            return run_stats

        fallback_messages = (
            self._messages_from_complete_thread_scan(
                channel=channel,
                messages=list(thread_outcome.scanned_messages),
                fetch_since=options.fetch_since,
                max_messages=options.max_messages,
            )
            if thread_outcome.scanned_messages is not None
            else None
        )
        archived_assessment: ArchivedThreadAssessment | None = None
        if is_archived_thread(channel):
            archived_assessment = archived_thread_coordinator.assess(
                channel=channel,
                guild=context.guild,
                parent=context.parent,
                mode=options.archived_thread_cleanup,
            )
            if not archived_assessment.should_scan:
                run_stats.archived_threads_skipped_count += 1
                log = (
                    self.logger.diagnostic
                    if options.archived_thread_cleanup == "skip"
                    else self.logger.info
                )
                log(
                    "Skipping archived thread %s without scanning messages: %s.",
                    channel_str(channel),
                    archived_assessment.reason,
                )
                return run_stats

        force_buffer = (
            fallback_messages is not None
            or archived_assessment is not None
        )
        if force_buffer:
            if fallback_messages is not None:
                messages: Iterable[DiscordMessage] = fallback_messages
                buffer_elapsed = thread_outcome.scan_elapsed
            else:
                messages, buffer_elapsed = self._prepare_channel_messages(
                    channel=channel,
                    fetch_sleep_time_range=options.fetch_sleep_time_range,
                    fetch_since=options.fetch_since,
                    max_messages=options.max_messages,
                    buffer_channel_messages=True,
                    dry_run=options.dry_run,
                )
        else:
            messages, buffer_elapsed = self._prepare_channel_messages(
                channel=channel,
                fetch_sleep_time_range=options.fetch_sleep_time_range,
                fetch_since=options.fetch_since,
                max_messages=options.max_messages,
                buffer_channel_messages=options.buffer_channel_messages,
                dry_run=options.dry_run,
            )

        channel_plan = None
        if options.buffer_channel_messages or force_buffer:
            channel_plan = self._planner(
                cutoff_time=options.cutoff_time,
                delete_reactions=options.delete_reactions,
            ).build_channel_plan(messages)
            if not options.dry_run:
                self.reporter.log_buffered_channel_pre_execution(
                    buffer_elapsed=buffer_elapsed or 0.0,
                    channel_plan=channel_plan,
                    delete_sleep_time_range=options.delete_sleep_time_range,
                )

        activation = None
        resume_archived_thread_callback: Callable[[], bool] | None = None
        archived_transition_action_count = 0
        if (
            archived_assessment is not None
            and channel_plan is not None
            and channel_plan.action_count > 0
        ):
            run_stats.archived_threads_planned_count += 1
            archived_transition_action_count = 2
            if options.dry_run:
                restoration = (
                    "and would restore it to archived state"
                    if archived_assessment.restore_expected
                    else "and restoration is not guaranteed; it may remain active"
                )
                self.logger.progress(
                    "Would attempt to unarchive thread %s for planned cleanup %s.",
                    channel_str(channel),
                    restoration,
                    indent=1,
                    prefix="-",
                )
            else:
                activation = archived_thread_coordinator.activate(
                    channel,
                    archived_assessment,
                )
                if not activation.opened:
                    run_stats.archived_threads_open_failed_count += 1
                    message_actions = sum(
                        action.kind == ActionKind.DELETE_MESSAGE
                        for action in channel_plan.actions
                    )
                    reaction_actions = sum(
                        action.kind == ActionKind.DELETE_REACTION
                        for action in channel_plan.actions
                    )
                    self.logger.warning(
                        "Skipping planned cleanup in archived thread %s because it could not be "
                        "unarchived (%s message deletion(s), %s reaction removal(s) not attempted).",
                        channel_str(channel),
                        message_actions,
                        reaction_actions,
                    )
                    return run_stats
                run_stats.archived_threads_opened_count += 1

                def handle_archived_thread() -> bool:
                    nonlocal activation
                    if activation is None:
                        return False
                    result = (
                        archived_thread_coordinator.resume_after_likely_auto_archive(
                            channel,
                            activation,
                        )
                    )
                    activation = result.activation
                    if result.retry_action:
                        run_stats.archived_threads_auto_reopened_count += 1
                    return result.retry_action

                resume_archived_thread_callback = handle_archived_thread

        try:
            preserved_msg_ids, stats, action_elapsed = (
                self.delete_messages_older_than(
                    messages=messages,
                    cutoff_time=options.cutoff_time,
                    delete_sleep_time_range=options.delete_sleep_time_range,
                    dry_run=options.dry_run,
                    channel_plan=channel_plan,
                    delete_reactions=options.delete_reactions,
                    resume_archived_thread=resume_archived_thread_callback,
                )
            )
        finally:
            if activation is not None and activation.opened:
                restore_outcome = archived_thread_coordinator.restore(
                    channel,
                    activation,
                )
                if restore_outcome == ThreadRestoreOutcome.RESTORED:
                    run_stats.archived_threads_restored_count += 1
                elif restore_outcome == ThreadRestoreOutcome.ABSENT:
                    run_stats.archived_threads_absent_count += 1
                else:
                    run_stats.archived_threads_left_active_count += 1

        if self.preserve_cache:
            self.preserve_cache.set_ids(
                channel_id=channel["id"],
                message_ids=preserved_msg_ids,
            )
            self.preserve_cache.save()

        run_stats.add_channel_stats(stats)
        channel_elapsed = time.monotonic() - channel_started_at
        get_last_fetch_summary = getattr(
            self.api,
            "get_last_fetch_summary",
            None,
        )
        raw_fetch_summary = (
            get_last_fetch_summary(channel["id"])
            if callable(get_last_fetch_summary)
            else None
        )
        fetch_summary = (
            raw_fetch_summary if isinstance(raw_fetch_summary, dict) else None
        )
        if options.dry_run:
            action_count = (
                stats.deleted_count
                + stats.reactions_removed_count
                + archived_transition_action_count
            )
            channel_execute_estimate = self.reporter.format_duration(
                self.reporter.estimate_action_count_duration(
                    action_count,
                    options.delete_sleep_time_range,
                )
            )
            channel_total_estimate = self.reporter.format_duration(
                channel_elapsed
                + self.reporter.estimate_action_count_duration(
                    action_count,
                    options.delete_sleep_time_range,
                )
            )
            self.reporter.log_dry_run_channel_summary(
                stats=stats,
                fetch_summary=fetch_summary,
                channel_elapsed=channel_elapsed,
                channel_execute_estimate=channel_execute_estimate,
                channel_total_estimate=channel_total_estimate,
                delete_reactions=options.delete_reactions,
                channel_plan=channel_plan,
            )
            return run_stats

        self.reporter.log_executed_channel_summary(
            stats=stats,
            fetch_summary=fetch_summary,
            channel_elapsed=channel_elapsed,
            action_elapsed=action_elapsed,
            delete_reactions=options.delete_reactions,
            channel_plan=channel_plan,
        )
        return run_stats

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

    def _planner(
        self,
        cutoff_time: datetime,
        delete_reactions: bool,
    ) -> CleanupPlanner:
        """Create the pure planner for one cleanup policy evaluation."""
        return CleanupPlanner(
            user_id=self.user_id,
            policy=CleanupPolicy(
                cutoff_time=cutoff_time,
                preserve_n=self.preserve_n,
                preserve_n_mode=self.preserve_n_mode,
                delete_reactions=delete_reactions,
            ),
        )

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
