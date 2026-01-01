# delete_me_discord/cleaner.py
import os
import time
import random
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Generator, Tuple, Optional, Union, Set
import logging

from .api import DiscordAPI
from .utils import channel_str, should_include_channel, format_timestamp
from .preserve_cache import PreserveCache

class MessageCleaner:
    def __init__(
        self,
        api: DiscordAPI,
        user_id: Optional[str] = None,
        include_ids: Optional[List[str]] = None,
        exclude_ids: Optional[List[str]] = None,
        preserve_last: timedelta = timedelta(weeks=2),
        preserve_n: int = 0,
        preserve_n_mode: str = "mine",
        preserve_cache: Optional[PreserveCache] = None
    ):
        """
        Initializes the MessageCleaner.

        Args:
            api (DiscordAPI): An instance of DiscordAPI.
            user_id (Optional[str]): The user ID whose messages will be targeted. If not provided and not set in the environment, it will be fetched via the API token.
            include_ids (Optional[List[str]]): IDs to include.
            exclude_ids (Optional[List[str]]): IDs to exclude.
            preserve_last (timedelta): Preserve recent messages in each channel within the last preserve_last regardless of preserve_n.
            preserve_n (int): Number of recent messages to preserve in each channel regardless of preserve_last.
            preserve_n_mode (str): How to count the last N messages to keep: 'mine' (only your deletable messages) or 'all' (last N messages in the channel).
            preserve_cache (Optional[PreserveCache]): Optional cache to track preserved message IDs between runs.

        Raises:
            ValueError: If both include_ids and exclude_ids contain overlapping IDs.
            ValueError: If user_id is not provided and not set in environment variables.
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
            raise ValueError("User ID not provided. Set DISCORD_USER_ID environment variable, pass as an argument, or ensure the token can fetch /users/@me.")

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

    def get_all_channels(self) -> List[Dict[str, Any]]:
        """
        Retrieves all relevant channels based on include and exclude IDs.

        Returns:
            List[Dict[str, Any]]: A list of channel dictionaries.
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

        self.logger.info("Total channels to process: %s", len(all_channels))
        return all_channels

    def _should_include_channel(self, channel: Dict[str, Any]) -> bool:
        """
        Determines if a channel should be included based on include and exclude IDs.

        Args:
            channel (Dict[str, Any]): The channel data.

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
        channel: Dict[str, Any],
        fetch_sleep_time_range: Tuple[float, float],
        fetch_since: Optional[datetime],
        max_messages: Union[int, float]
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Fetches all messages from a given channel authored by the specified user.

        Args:
            channel (Dict[str, Any]): The channel dictionary.
            fetch_sleep_time_range (Tuple[float, float]): Range for sleep time between fetch requests.
            fetch_since (Optional[datetime]): Only fetch messages newer than this timestamp.
            max_messages (Union[int, float]): Maximum number of messages to fetch.

        Yields:
            Dict[str, Any]: Message data.
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
        messages: Generator[Dict[str, Any], None, None],
        cutoff_time: datetime,
        delete_sleep_time_range: Tuple[float, float],
        dry_run: bool = False,
        delete_reactions: bool = False
    ) -> Tuple[List[str], Dict[str, int]]:
        """
        Deletes messages older than the cutoff time.

        Args:
            messages (Generator[Dict[str, Any], None, None]): Generator of message data.
            cutoff_time (datetime): The cutoff datetime; messages older than this will be deleted.
            delete_sleep_time_range (Tuple[float, float]): Range for sleep time between deletion attempts.
            dry_run (bool): If True, simulate deletions without calling the API.
            delete_reactions (bool): If True, remove the user's reactions on messages encountered.

        Returns:
            Tuple[List[str], Dict[str, int]]: List of preserved message IDs and statistics dictionary.
        """
        preserve_n_count = 0
        preserve_count_active = self.preserve_n > 0
        preserved_msg_ids = []
        stats = {
            "deleted_count": 0,
            "preserved_deletable_count": 0,
            "reactions_removed_count": 0,
            "preserved_reactions_count": 0,
        }

        for message in messages:
            message_id = message["message_id"]
            timestamp_str = message["timestamp"]
            message_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            is_author = message["author_id"] == self.user_id
            is_deletable = is_author and message["type"].deletable

            # Track how many messages are inside the preservation window depending on mode
            if preserve_count_active and (self.preserve_n_mode == "all" or is_deletable):
                preserve_n_count += 1

            in_preserve_n_count_window = preserve_count_active and preserve_n_count <= self.preserve_n
            in_preserve_window = in_preserve_n_count_window or message_time >= cutoff_time

            if in_preserve_window:
                if is_deletable:
                    self.logger.debug("Preserving deletable message %s sent at %s UTC.", message_id, format_timestamp(message_time))
                    stats["preserved_deletable_count"] += 1
                    preserved_msg_ids.append(message_id)
                elif delete_reactions:
                    my_reaction_count = sum(1 for reaction in (message.get("reactions") or []) if reaction.get("me"))
                    if my_reaction_count > 0:
                        stats["preserved_reactions_count"] += my_reaction_count
                        preserved_msg_ids.append(message_id)
                continue

            if is_deletable:
                if dry_run:
                    self.logger.debug("Would delete message %s sent at %s UTC.", message_id, format_timestamp(message_time))
                    stats["deleted_count"] += 1
                    self.logger.debug("Dry run enabled; skipping API delete for %s.", message_id)
                else:
                    self.logger.debug("Deleting message %s sent at %s UTC.", message_id, format_timestamp(message_time))
                    success = self.api.delete_message(
                        channel_id=message["channel_id"],
                        message_id=message_id
                    )
                    if success:
                        stats["deleted_count"] += 1
                        sleep_time = random.uniform(*delete_sleep_time_range)
                        self.logger.debug("Sleeping for %.2f seconds after deletion.", sleep_time)
                        time.sleep(sleep_time)  # Sleep between deletions
                    else:
                        self.logger.warning("Failed to delete message %s in channel %s.", message_id, message.get("channel_id"))

            elif delete_reactions: 
                stats["reactions_removed_count"] += self._delete_reactions_for_message(
                    message=message,
                    delete_sleep_time_range=delete_sleep_time_range,
                    dry_run=dry_run
                )

        if dry_run:
            summary = (
                f"  - Would delete={stats['deleted_count']}, "
                f"preserve deletable={stats['preserved_deletable_count']}"
            )
            if delete_reactions:
                summary += (
                    f", remove reactions={stats['reactions_removed_count']}, "
                    f"preserve reactions={stats['preserved_reactions_count']}"
                )
            self.logger.info(summary)
        else:
            summary = (
                f"  - Deleted={stats['deleted_count']}, "
                f"preserved deletable={stats['preserved_deletable_count']}"
            )
            if delete_reactions:
                summary += (
                    f", removed reactions={stats['reactions_removed_count']}, "
                    f"preserved reactions={stats['preserved_reactions_count']}"
                )
            self.logger.info(summary)

        return preserved_msg_ids, stats

    def clean_messages(
        self,
        dry_run: bool = False,
        fetch_sleep_time_range: Tuple[float, float] = (0.2, 0.5),
        delete_sleep_time_range: Tuple[float, float] = (1.5, 2),
        fetch_since: Optional[datetime] = None,
        max_messages: Union[int, float] = float("inf"),
        delete_reactions: bool = False
    ) -> int:
        """
        Cleans messages based on the specified criteria.

        Args:
            dry_run (bool): If True, messages will not be deleted.
            fetch_sleep_time_range (Tuple[float, float]): Range for sleep time between fetch requests.
            delete_sleep_time_range (Tuple[float, float]): Range for sleep time between deletion attempts.
            fetch_since (Optional[datetime]): Only fetch messages newer than this timestamp.
            max_messages (Union[int, float]): Maximum number of messages to fetch per channel.
            delete_reactions (bool): If True, remove the user's reactions on messages encountered.

        Returns:
            int: Total number of messages deleted.
        """
        total_stats = {
            "deleted_count": 0,
            "preserved_deletable_count": 0,
            "reactions_removed_count": 0,
            "preserved_reactions_count": 0,
        }

        cutoff_time = datetime.now(timezone.utc) - self.preserve_last
        self.logger.info("Deleting messages older than %s UTC.", format_timestamp(cutoff_time))
        if fetch_since:
            self.logger.info("Fetching messages not older than %s UTC.", format_timestamp(fetch_since))

        channels = self.get_all_channels()

        if dry_run:
            self.logger.info("Dry run mode enabled. Messages will be fetched and evaluated but not deleted.")

        for channel in channels:
            self.logger.info("Processing channel: %s.", channel_str(channel))
            messages = self.fetch_all_messages(
                channel=channel,
                fetch_sleep_time_range=fetch_sleep_time_range,
                fetch_since=fetch_since,
                max_messages=max_messages
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
            preserved_msg_ids, stats = self.delete_messages_older_than(
                messages=messages,
                cutoff_time=cutoff_time,
                delete_sleep_time_range=delete_sleep_time_range,
                dry_run=dry_run,
                delete_reactions=delete_reactions
            )

            if self.preserve_cache:
                self.preserve_cache.set_ids(channel_id=channel["id"], message_ids=preserved_msg_ids)

            total_stats["deleted_count"] += stats["deleted_count"]
            total_stats["preserved_deletable_count"] += stats["preserved_deletable_count"]
            total_stats["reactions_removed_count"] += stats["reactions_removed_count"]
            total_stats["preserved_reactions_count"] += stats["preserved_reactions_count"]

        if dry_run:
            total_summary = (
                f"Summary: Would delete={total_stats['deleted_count']}, "
                f"preserve deletable={total_stats['preserved_deletable_count']}"
            )
            if delete_reactions:
                total_summary += (
                    f", remove reactions={total_stats['reactions_removed_count']}, "
                    f"preserve reactions={total_stats['preserved_reactions_count']}"
                )
            self.logger.info(total_summary)
        else:
            total_summary = (
                f"Summary: Deleted={total_stats['deleted_count']}, "
                f"preserved deletable={total_stats['preserved_deletable_count']}"
            )
            if delete_reactions:
                total_summary += (
                    f", removed reactions={total_stats['reactions_removed_count']}, "
                    f"preserved reactions={total_stats['preserved_reactions_count']}"
                )
            self.logger.info(total_summary)


        return total_stats["deleted_count"]

    def _delete_reactions_for_message(
        self,
        message: Dict[str, Any],
        delete_sleep_time_range: Tuple[float, float],
        dry_run: bool
    ) -> int:
        """
        Deletes the user's reactions from a message.

        Args:
            message (Dict[str, Any]): Message data containing reactions.
            delete_sleep_time_range (Tuple[float, float]): Range for sleep time between deletions.
            dry_run (bool): If True, simulate deletions without calling the API.

        Returns:
            int: Number of reactions removed.
        """
        reactions = message.get("reactions") or []
        removed = 0
        for reaction in reactions:
            if not reaction.get("me"):
                continue
            emoji = reaction.get("emoji", {})
            emoji_name = emoji.get("name", "unknown")
            if dry_run:
                removed += 1
                self.logger.debug(
                    "Would remove reaction %s from message %s in channel %s.",
                    emoji_name, message["message_id"], message["channel_id"]
                )
                continue

            success = self.api.delete_own_reaction(
                channel_id=message["channel_id"],
                message_id=message["message_id"],
                emoji=emoji
            )
            if success:
                removed += 1
                sleep_time = random.uniform(*delete_sleep_time_range)
                self.logger.debug("Sleeping for %.2f seconds after reaction deletion.", sleep_time)
                time.sleep(sleep_time)
            else:
                self.logger.warning(
                    "Failed to delete reaction %s on message %s in channel %s.",
                    emoji_name, message["message_id"], message["channel_id"]
                )

        return removed

    def _merge_cached_messages(
        self,
        channel: Dict[str, Any],
        main_messages: Generator[Dict[str, Any], None, None],
        cached_ids: List[str],
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Merge the main message stream (newest -> oldest) with cached IDs, preserving
        descending snowflake order and avoiding duplicate processing. Prefers the main
        stream if both sources contain the same message ID.
        """

        if cached_ids and int(cached_ids[0]) < int(cached_ids[-1]):
            raise ValueError("Cached message IDs must be in descending order (newest first).")
        cache: List[Tuple[int, str]] = [(int(mid), mid) for mid in cached_ids]
        cache_idx = 0
        seen_ids: Set[str] = set()

        def emit_cache_until(main_id_int: int) -> Generator[Dict[str, Any], None, None]:
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
