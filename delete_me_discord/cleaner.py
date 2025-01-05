# delete_me_discord/cleaner.py
import os
import time
import random
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Generator, Tuple, Optional
import logging

from .api import DiscordAPI
from .utils import channel_str


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
            user_id (Optional[str]): The user ID whose messages will be targeted.
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
            raise ValueError("User ID not provided. Set DISCORD_USER_ID environment variable or pass as an argument.")

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
        channel_id = channel.get("id")
        guild_id = channel.get("guild_id")
        parent_id = channel.get("parent_id")

        # Exclude logic
        if self.exclude_ids.intersection({channel_id, guild_id, parent_id}):
            self.logger.debug("Excluding channel: %s.", channel_str(channel))
            return False

        # Include logic
        if self.include_ids:
            if not self.include_ids.intersection({channel_id, guild_id, parent_id}):
                self.logger.debug("Excluding channel not in include_ids: %s.", channel_str(channel))
                return False

        return True

    def fetch_all_messages(self, channel: Dict[str, Any], fetch_sleep_time_range: Tuple[float, float]) -> Generator[Dict[str, Any], None, None]:
        """
        Fetches all messages from a given channel authored by the specified user.

        Args:
            channel (Dict[str, Any]): The channel dictionary.
            fetch_sleep_time_range (Tuple[float, float]): Range for sleep time between fetch requests.

        Yields:
            Dict[str, Any]: Message data.
        """
        self.logger.info("Fetching messages from %s.", channel_str(channel))
        fetched_count = 0

        for message in self.api.fetch_messages(channel["id"], fetch_sleep_time_range=fetch_sleep_time_range):
            yield message
            fetched_count += 1

        self.logger.info("Fetched %s messages from %s.", fetched_count, channel_str(channel))

    def delete_messages_older_than(
        self,
        messages: Generator[Dict[str, Any], None, None],
        cutoff_time: datetime,
        delete_sleep_time_range: Tuple[float, float]
    ) -> Tuple[int, int]:
        """
        Deletes messages older than the cutoff time.

        Args:
            messages (Generator[Dict[str, Any], None, None]): Generator of message data.
            cutoff_time (datetime): The cutoff datetime; messages older than this will be deleted.
            delete_sleep_time_range (Tuple[float, float]): Range for sleep time between deletion attempts.

        Returns:
            Tuple[int, int]: Number of messages deleted and ignored.
        """
        deleted_count = 0
        preserved_count = 0
        deleteable = 0
        for message in messages:
            message_id = message["message_id"]
            timestamp_str = message["timestamp"]
            message_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            # skip non user messages
            if message["author_id"] != self.user_id:
                self.logger.debug("Skipping message %s not authored by user.", message["message_id"])
                continue
            if not message["type"].deletable:
                self.logger.debug("Skipping non-deletable message of type %s.", message["type"])
                continue

            deleteable += 1
            if deleteable < self.preserve_n or message_time >= cutoff_time:
                self.logger.debug("Preserving message %s sent at %s UTC.", message_id, message_time.isoformat())
                preserved_count += 1
                continue

            self.logger.info("Deleting message %s sent at %s UTC.", message_id, message_time.isoformat())
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

        return deleted_count, preserved_count

    def clean_messages(
        self,
        dry_run: bool = False,
        fetch_sleep_time_range: Tuple[float, float] = (0.2, 0.5),
        delete_sleep_time_range: Tuple[float, float] = (1.5, 2)
    ) -> int:
        """
        Cleans messages based on the specified criteria.

        Args:
            dry_run (bool): If True, messages will not be deleted.
            fetch_sleep_time_range (Tuple[float, float]): Range for sleep time between fetch requests.
            delete_sleep_time_range (Tuple[float, float]): Range for sleep time between deletion attempts.

        Returns:
            int: Total number of messages deleted.
        """
        total_deleted = 0
        cutoff_time = datetime.now(timezone.utc) - self.preserve_last
        self.logger.info("Deleting messages older than %s UTC.", cutoff_time.isoformat())

        channels = self.get_all_channels()

        if dry_run:
            self.logger.info("Dry run mode enabled. Messages will not be deleted.")
            for channel in channels:
                self.logger.info(channel_str(channel))
            return total_deleted

        for channel in channels:
            self.logger.debug("Processing channel: %s.", channel_str(channel))
            messages = self.fetch_all_messages(channel, fetch_sleep_time_range)
            deleted, preserved = self.delete_messages_older_than(
                messages=messages,
                cutoff_time=cutoff_time,
                delete_sleep_time_range=delete_sleep_time_range
            )
            self.logger.info("Preserved %s messages in %s.", preserved, channel_str(channel))
            self.logger.info("Deleted %s messages from channel %s.", deleted, channel_str(channel))
            total_deleted += deleted

        self.logger.info("Total messages deleted: %s", total_deleted)
        return total_deleted
