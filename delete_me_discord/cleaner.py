# delete_me_discord/cleaner.py
import os
import time
import random
from datetime import datetime, timezone, timedelta
from typing import Iterable, List, Generator, Tuple, Optional, Union, Set
import logging

from .api import DiscordAPI
from .models import ActionKind, ChannelPlan, DiscordChannel, DiscordEmoji, DiscordMessage, MessageDecision, MessageFacts, PlannedAction
from .utils import channel_str, should_include_channel, format_timestamp
from .privacy import sensitive
from .preserve_cache import PreserveCache


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
        preserve_cache: Optional[PreserveCache] = None
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

        self.include_ids = set(include_ids) if include_ids else set()
        self.exclude_ids = set(exclude_ids) if exclude_ids else set()
        self.preserve_last = preserve_last
        self.preserve_n = preserve_n
        if preserve_n_mode not in {"mine", "all"}:
            raise ValueError("preserve_n_mode must be 'mine' or 'all'.")
        self.preserve_n_mode = preserve_n_mode
        self.logger = logging.getLogger(self.__class__.__name__)
        self.preserve_cache = preserve_cache

        if self.include_ids.intersection(self.exclude_ids):
            raise ValueError("Include and exclude IDs must be disjoint.")

    def get_all_channels(self) -> List[DiscordChannel]:
        """
        Retrieves all relevant channels based on include and exclude IDs.

        Returns:
            List[DiscordChannel]: Channels eligible for processing.
        """
        all_channels = []
        channel_types = {0: "GuildText", 1: "DM", 3: "GroupDM"}

        # Fetch guilds and their channels
        guilds = self.api.get_guilds()
        guild_ids = [guild["id"] for guild in guilds]
        guild_channels = self.api.get_guild_channels_multiple(guild_ids)

        # Fetch root channels (DMs)
        root_channels = self.api.get_root_channels()

        # Process root channels
        for channel in root_channels:
            if channel.get("type") not in channel_types:
                self.logger.debug("Skipping unknown channel type: %s", channel.get("type"))
                continue
            if not self._should_include_channel(channel):
                continue
            all_channels.append(channel)
            self.logger.debug("Included channel: %s.", channel_str(channel))

        # Process guild channels
        for channel in guild_channels:
            if channel.get("type") not in channel_types:
                self.logger.debug("Skipping unknown channel type: %s", channel.get("type"))
                continue
            if not self._should_include_channel(channel):
                continue
            all_channels.append(channel)
            self.logger.debug("Included channel: %s.", channel_str(channel))

        self.logger.progress("Channels to process: %s", len(all_channels))
        return all_channels

    def _should_include_channel(self, channel: DiscordChannel) -> bool:
        """
        Determines if a channel should be included based on include and exclude IDs.

        Args:
            channel (DiscordChannel): The channel payload.

        Returns:
            bool: True if the channel should be included, False otherwise.
        """
        allowed = should_include_channel(
            channel=channel,
            include_ids=self.include_ids,
            exclude_ids=self.exclude_ids
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
            fetch_sleep_time_range (Tuple[float, float]): Range for sleep time between fetch requests.
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
            delete_sleep_time_range (Tuple[float, float]): Range for sleep time between deletion attempts.
            dry_run (bool): If True, simulate deletions without calling the API.
            delete_reactions (bool): If True, remove the user's reactions on messages encountered.
            channel_plan (Optional[ChannelPlan]): Optional precomputed channel plan.

        Returns:
            Tuple[List[str], dict[str, int], float]: Preserved message IDs, statistics, and action-phase elapsed time.
        """
        preserved_msg_ids = []
        stats = {
            "message_count": 0,
            "deleted_count": 0,
            "preserved_deletable_count": 0,
            "reactions_removed_count": 0,
            "preserved_reactions_count": 0,
        }
        plan = channel_plan or ChannelPlan(
            decisions=tuple(
                self._iter_message_decisions(
                    messages=messages,
                    cutoff_time=cutoff_time,
                    delete_reactions=delete_reactions,
                )
            )
        )
        total_actions = plan.action_count

        action_start = time.monotonic()
        for decision in plan.decisions:
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
                    delete_sleep_time_range=delete_sleep_time_range,
                    dry_run=dry_run,
                    facts=facts,
                )
                if action.kind == ActionKind.DELETE_MESSAGE:
                    if executed:
                        stats["deleted_count"] += 1
            if reaction_actions:
                stats["reactions_removed_count"] += self._execute_reaction_actions(
                    actions=reaction_actions,
                    delete_sleep_time_range=delete_sleep_time_range,
                    dry_run=dry_run,
                )
        action_elapsed = time.monotonic() - action_start

        return preserved_msg_ids, stats, action_elapsed

    def clean_messages(
        self,
        dry_run: bool = False,
        fetch_sleep_time_range: Tuple[float, float] = (0.2, 0.5),
        delete_sleep_time_range: Tuple[float, float] = (1.5, 2),
        fetch_since: Optional[datetime] = None,
        max_messages: Union[int, float] = float("inf"),
        buffer_channel_messages: bool = False,
        delete_reactions: bool = False
    ) -> int:
        """
        Run the cleaner across all eligible channels.

        Args:
            dry_run (bool): If True, messages will not be deleted.
            fetch_sleep_time_range (Tuple[float, float]): Range for sleep time between fetch requests.
            delete_sleep_time_range (Tuple[float, float]): Range for sleep time between deletion attempts.
            fetch_since (Optional[datetime]): Only fetch messages newer than this timestamp.
            max_messages (Union[int, float]): Maximum number of messages to fetch per channel.
            buffer_channel_messages (bool): If True, fully buffer one channel before evaluation.
            delete_reactions (bool): If True, remove the user's reactions on messages encountered.

        Returns:
            int: Total number of messages deleted.
        """
        run_started_at = time.monotonic()
        total_stats = {
            "deleted_count": 0,
            "preserved_deletable_count": 0,
            "reactions_removed_count": 0,
            "preserved_reactions_count": 0,
        }

        cutoff_time = datetime.now(timezone.utc) - self.preserve_last
        if self.preserve_last > timedelta(0):
            self.logger.info("Deleting messages older than %s UTC.", format_timestamp(cutoff_time))
        if fetch_since:
            self.logger.info("Fetching messages not older than %s UTC.", format_timestamp(fetch_since))

        channels = self.get_all_channels()

        if dry_run:
            self.logger.info("Dry run enabled. Messages will be fetched and evaluated but not deleted.")

        for channel in channels:
            channel_started_at = time.monotonic()
            self.logger.progress("Processing channel: %s.", channel_str(channel))
            messages, buffer_elapsed = self._prepare_channel_messages(
                channel=channel,
                fetch_sleep_time_range=fetch_sleep_time_range,
                fetch_since=fetch_since,
                max_messages=max_messages,
                buffer_channel_messages=buffer_channel_messages,
                dry_run=dry_run,
            )
            channel_plan = None
            if buffer_channel_messages:
                channel_plan = self._build_channel_plan(
                    messages=messages,
                    cutoff_time=cutoff_time,
                    delete_reactions=delete_reactions,
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
                delete_reactions=delete_reactions
            )
            if self.preserve_cache:
                self.preserve_cache.set_ids(channel_id=channel["id"], message_ids=preserved_msg_ids)

            total_stats["deleted_count"] += stats["deleted_count"]
            total_stats["preserved_deletable_count"] += stats["preserved_deletable_count"]
            total_stats["reactions_removed_count"] += stats["reactions_removed_count"]
            total_stats["preserved_reactions_count"] += stats["preserved_reactions_count"]
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

        run_elapsed = time.monotonic() - run_started_at
        if dry_run:
            execute_estimate_seconds = self._estimate_action_count_duration(
                total_stats["deleted_count"] + total_stats["reactions_removed_count"],
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
                f"{total_stats['preserved_deletable_count']} kept"
            )
            if delete_reactions:
                total_summary += (
                    f", reactions {total_stats['reactions_removed_count']} deleted / "
                    f"{total_stats['preserved_reactions_count']} kept"
                )
            self.logger.info(total_summary)

        return total_stats["deleted_count"]

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
        is_deletable = is_author and message["type"].deletable
        my_reactions = tuple(
            reaction for reaction in (message.get("reactions") or []) if delete_reactions and reaction.get("me")
        )
        return MessageFacts(
            message=message,
            message_time=message_time,
            is_author=is_author,
            is_deletable=is_deletable,
            my_reactions=my_reactions,
        )

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
                            emoji=reaction.get("emoji"),
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
        """Estimate execution time from a raw action count and configured sleep range."""
        average_sleep = sum(delete_sleep_time_range) / 2
        return action_count * average_sleep

    def _format_duration(self, seconds: float) -> str:
        """Format a duration in seconds as HH:MM:SS."""
        whole_seconds = max(0, int(round(seconds)))
        hours, remainder = divmod(whole_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

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
                f"{stats['preserved_deletable_count']} kept"
            )
            reaction_summary = (
                f", reactions {stats['reactions_removed_count']} deleted / "
                f"{stats['preserved_reactions_count']} kept"
            )

        if delete_reactions:
            summary += reaction_summary
        elif dry_run:
            summary += ", reactions 0 delete / 0 keep"
        else:
            summary += ", reactions 0 deleted / 0 kept"

        if dry_run and channel_plan is not None:
            summary += f", buffered messages={channel_plan.buffered_message_count}"
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
        delete_sleep_time_range: Tuple[float, float],
        dry_run: bool,
        facts: Optional[MessageFacts] = None,
    ) -> bool:
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
                return True

            self.logger.event(
                "Deleting message %s.",
                sensitive(action.message_id),
                indent=1,
                prefix="-",
            )
            self._log_message_detail(facts)
            success = self.api.delete_message(
                channel_id=action.channel_id,
                message_id=action.message_id,
            )
            if success:
                sleep_time = random.uniform(*delete_sleep_time_range)
                self.logger.diagnostic("Sleeping for %.2f seconds after deletion.", sleep_time)
                time.sleep(sleep_time)
            else:
                self.logger.warning(
                    "Failed to delete message %s in channel %s.",
                    sensitive(action.message_id),
                    sensitive(action.channel_id),
                )
            return success

        emoji: DiscordEmoji = action.emoji or {}
        emoji_name = emoji.get("name", "unknown")
        if dry_run:
            self.logger.event(
                "Would delete reaction from message %s.",
                sensitive(action.message_id),
                indent=1,
                prefix="-",
            )
            self._log_reaction_detail(emoji_name)
            return True

        self.logger.event(
            "Deleting reaction from message %s.",
            sensitive(action.message_id),
            indent=1,
            prefix="-",
        )
        self._log_reaction_detail(emoji_name)
        success = self.api.delete_own_reaction(
            channel_id=action.channel_id,
            message_id=action.message_id,
            emoji=emoji,
        )
        if success:
            sleep_time = random.uniform(*delete_sleep_time_range)
            self.logger.diagnostic("Sleeping for %.2f seconds after reaction deletion.", sleep_time)
            time.sleep(sleep_time)
        else:
            self.logger.warning(
                "Failed to delete reaction %s on message %s in channel %s.",
                sensitive(emoji_name, full=True),
                sensitive(action.message_id),
                sensitive(action.channel_id),
        )
        return success

    def _execute_reaction_actions(
        self,
        actions: List[PlannedAction],
        delete_sleep_time_range: Tuple[float, float],
        dry_run: bool,
    ) -> int:
        """Execute one or more reaction deletions for the same message as one visible event block."""
        if not actions:
            return 0

        message_id = actions[0].message_id
        emoji_names = [(action.emoji or {}).get("name", "unknown") for action in actions]
        reaction_count = len(actions)
        reaction_label = "reaction" if reaction_count == 1 else f"{reaction_count} reactions"

        if dry_run:
            self.logger.event(
                "Would delete %s from message %s.",
                reaction_label,
                sensitive(message_id),
                indent=1,
                prefix="-",
            )
            self._log_reaction_detail(emoji_names)
            return reaction_count

        self.logger.event(
            "Deleting %s from message %s.",
            reaction_label,
            sensitive(message_id),
            indent=1,
            prefix="-",
        )
        self._log_reaction_detail(emoji_names)

        deleted_count = 0
        for action in actions:
            emoji: DiscordEmoji = action.emoji or {}
            emoji_name = emoji.get("name", "unknown")
            success = self.api.delete_own_reaction(
                channel_id=action.channel_id,
                message_id=action.message_id,
                emoji=emoji,
            )
            if success:
                deleted_count += 1
                sleep_time = random.uniform(*delete_sleep_time_range)
                self.logger.diagnostic("Sleeping for %.2f seconds after reaction deletion.", sleep_time)
                time.sleep(sleep_time)
            else:
                self.logger.warning(
                    "Failed to delete reaction %s on message %s in channel %s.",
                    sensitive(emoji_name, full=True),
                    sensitive(action.message_id),
                    sensitive(action.channel_id),
                )

        return deleted_count

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
        main_messages: Generator[DiscordMessage, None, None],
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
