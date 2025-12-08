# delete_me_discord/cleaner.py
import os
import time
import random
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Generator, Tuple, Optional, Union
import logging

from .api import DiscordAPI
from .utils import channel_str, should_include_channel, format_timestamp


class MessageCleaner:
    def __init__(
        self,
        api: DiscordAPI,
        user_id: Optional[str] = None,
        include_ids: Optional[List[str]] = None,
        exclude_ids: Optional[List[str]] = None,
        preserve_last: timedelta = timedelta(weeks=2),
        preserve_n: int = 0
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
        self.logger = logging.getLogger(self.__class__.__name__)

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
        self.logger.info("Fetching messages from %s.", channel_str(channel))
        fetched_count = 0

        for message in self.api.fetch_messages(
            channel["id"],
            fetch_sleep_time_range=fetch_sleep_time_range,
            fetch_since=fetch_since,
            max_messages=max_messages,
        ):
            yield message
            fetched_count += 1

        self.logger.info("Fetched %s messages from %s.", fetched_count, channel_str(channel))

    def delete_messages_older_than(
        self,
        messages: Generator[Dict[str, Any], None, None],
        cutoff_time: datetime,
        delete_sleep_time_range: Tuple[float, float],
        dry_run: bool = False,
        delete_reactions: bool = False
    ) -> Tuple[int, int, int]:
        """
        Deletes messages older than the cutoff time.

        Args:
            messages (Generator[Dict[str, Any], None, None]): Generator of message data.
            cutoff_time (datetime): The cutoff datetime; messages older than this will be deleted.
            delete_sleep_time_range (Tuple[float, float]): Range for sleep time between deletion attempts.
            dry_run (bool): If True, simulate deletions without calling the API.
            delete_reactions (bool): If True, remove the user's reactions on messages encountered.

        Returns:
            Tuple[int, int, int]: Number of messages deleted, preserved, and reactions removed.
        """
        deleted_count = 0
        preserved_count = 0
        deleteable = 0
        reactions_removed = 0
        for message in messages:
            message_id = message["message_id"]
            timestamp_str = message["timestamp"]
            message_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))

            # skip non user messages
            if message["author_id"] != self.user_id:
                if delete_reactions and message_time < cutoff_time and deleteable >= self.preserve_n:
                    reactions_removed += self._delete_reactions_for_message(
                        message=message,
                        delete_sleep_time_range=delete_sleep_time_range,
                        dry_run=dry_run
                    )
                self.logger.debug("Skipping message deletion for %s not authored by user.", message["message_id"])
                continue
            if not message["type"].deletable:
                self.logger.debug("Skipping non-deletable message of type %s.", message["type"])
                continue

            deleteable += 1
            if deleteable < self.preserve_n or message_time >= cutoff_time:
                self.logger.debug("Preserving message %s sent at %s UTC.", message_id, format_timestamp(message_time))
                preserved_count += 1
                continue

            if dry_run:
                self.logger.info("Would delete message %s sent at %s UTC.", message_id, format_timestamp(message_time))
                deleted_count += 1
                self.logger.debug("Dry run enabled; skipping API delete for %s.", message_id)
            else:
                self.logger.info("Deleting message %s sent at %s UTC.", message_id, format_timestamp(message_time))
                success = self.api.delete_message(
                    channel_id=message["channel_id"],
                    message_id=message_id
                )
                if success:
                    deleted_count += 1
                    sleep_time = random.uniform(*delete_sleep_time_range)
                    self.logger.debug("Sleeping for %.2f seconds after deletion.", sleep_time)
                    time.sleep(sleep_time)  # Sleep between deletions
                else:
                    self.logger.warning("Failed to delete message %s in channel %s.", message_id, message.get("channel_id"))

        return deleted_count, preserved_count, reactions_removed

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
        total_deleted = 0
        total_reactions_removed = 0
        cutoff_time = datetime.now(timezone.utc) - self.preserve_last
        self.logger.info("Deleting messages older than %s UTC.", format_timestamp(cutoff_time))
        if fetch_since:
            self.logger.info("Fetching messages not older than %s UTC.", format_timestamp(fetch_since))

        channels = self.get_all_channels()

        if dry_run:
            self.logger.info("Dry run mode enabled. Messages will be fetched and evaluated but not deleted.")

        for channel in channels:
            self.logger.debug("Processing channel: %s.", channel_str(channel))
            messages = self.fetch_all_messages(
                channel=channel,
                fetch_sleep_time_range=fetch_sleep_time_range,
                fetch_since=fetch_since,
                max_messages=max_messages
            )
            deleted, preserved, reactions_removed = self.delete_messages_older_than(
                messages=messages,
                cutoff_time=cutoff_time,
                delete_sleep_time_range=delete_sleep_time_range,
                dry_run=dry_run,
                delete_reactions=delete_reactions
            )
            self.logger.info("Preserved %s messages in %s.", preserved, channel_str(channel))
            if dry_run:
                self.logger.info("Would delete %s messages from channel %s.", deleted, channel_str(channel))
            else:
                self.logger.info("Deleted %s messages from channel %s.", deleted, channel_str(channel))
            if delete_reactions:
                if dry_run:
                    self.logger.info("Would remove %s reactions in %s.", reactions_removed, channel_str(channel))
                else:
                    self.logger.info("Removed %s reactions in %s.", reactions_removed, channel_str(channel))
            total_deleted += deleted
            total_reactions_removed += reactions_removed

        if dry_run:
            self.logger.info("Total messages that would be deleted: %s", total_deleted)
            if delete_reactions:
                self.logger.info("Total reactions that would be removed: %s", total_reactions_removed)
        else:
            self.logger.info("Total messages deleted: %s", total_deleted)
            if delete_reactions:
                self.logger.info("Total reactions removed: %s", total_reactions_removed)
        return total_deleted

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
                self.logger.info(
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
