import os
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Generator, Optional
import logging
import argparse
import random
import requests
from dotenv import load_dotenv

DISCORD_TOKEN: Optional[str] = None
DISCORD_USER_ID: Optional[str] = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

def channel_str(channel: Dict[str, Any]) -> str:
    channel_types = {0: "GuildText", 1: "DM", 3: "GroupDM"}
    assert channel["type"] in channel_types, f"Unknown channel type: {channel['type']}"
    channel_name = channel.get("name")
    if not channel_name:
        recipients = channel.get("recipients", [])
        channel_name = ', '.join([recipient["username"] for recipient in recipients])
    return f"{channel_types[channel['type']]} {channel_name} (ID: {channel['id']})"

class FetchError(Exception):
    """Custom exception for fetch errors."""

class DiscordAPI:
    BASE_URL = "https://discord.com/api/v10"

    def __init__(self, token: Optional[str] = None):
        self._token = token

        self.headers = {
            "Authorization": f"{self.token}",
            "Content-Type": "application/json"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    def token(self) -> str:
        if self._token:
            return self._token
        elif DISCORD_TOKEN:
            return DISCORD_TOKEN
        elif token := os.getenv("DISCORD_TOKEN"):
            return token
        else:
            raise ValueError("No token found. Pass token as argument, set DISCORD_TOKEN variable, or set DISCORD_TOKEN environment variable.")


    def get_guilds(self, max_retries: int = 5) -> List[Dict[str, Any]]:
        url = f"{self.BASE_URL}/users/@me/guilds"
        response = self.session.get(url)
        if response.status_code == 200:
            self.logger.debug("Fetched guilds: %s", response.json())
            return response.json()
        elif response.status_code == 429:
            if max_retries == 1:
                raise FetchError("Max retries exceeded for fetching guilds.")
            retry_after = response.json().get("retry_after", 1) + random.randint(2,5) # Adding buffer
            self.logger.warning("Rate limit hit. Retrying after %s seconds.", (retry_after,))
            time.sleep(retry_after)
            return self.get_guilds(max_retries=max_retries-1)
        else:
            raise FetchError(f"Error fetching guilds: {response.status_code} - {response.text}")

    def get_guild_channels(self, guild_id: str, max_retries: int = 5) -> List[Dict[str, Any]]:
        assert max_retries > 0, "max_retries must be greater than 0"
        url = f"{self.BASE_URL}/guilds/{guild_id}/channels"
        response = self.session.get(url)
        if response.status_code == 200:
            self.logger.debug("Fetched channels for guild %s: %s", guild_id, response.json())
            return response.json()
        elif response.status_code == 429:
            if max_retries == 1:
                raise FetchError(f"Max retries exceeded for guild {guild_id}.")
            retry_after = response.json().get("retry_after", 1) + random.randint(2,5) # Adding buffer
            self.logger.warning("Rate limit hit. Retrying after %s seconds.", retry_after)
            time.sleep(retry_after)
            return self.get_guild_channels(guild_id, max_retries=max_retries-1)
        else:
            raise FetchError(f"Error fetching channels for guild {guild_id}: {response.status_code} - {response.text}")

    def get_guild_channels_multiple(self, guild_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Fetches channels for multiple guilds.

        Args:
            guild_ids (List[str]): List of guild IDs.

        Returns:
            List[Dict[str, Any]]: A list of channel dictionaries across all specified guilds.
        """
        all_channels = []
        for guild_id in guild_ids:
            try:
                channels = self.get_guild_channels(guild_id)
                all_channels.extend(channels)
                self.logger.debug("Fetched %s channels from guild %s.", len(channels), guild_id)
            except FetchError as e:
                self.logger.error(e)
        return all_channels

    def get_root_channels(self, max_retries = 5) -> List[Dict[str, Any]]:
        url = f"{self.BASE_URL}/users/@me/channels"
        response = self.session.get(url)
        if response.status_code == 200:
            self.logger.debug("Fetched DM channels: %s", response.json())
            return response.json()
        elif response.status_code == 429:
            if max_retries == 1:
                raise FetchError("Max retries exceeded for fetching DM channels.")
            retry_after = response.json().get("retry_after", 1) + random.randint(2,5)
            self.logger.warning("Rate limit hit. Retrying after %s seconds.", retry_after)
            time.sleep(retry_after)
            return self.get_root_channels(max_retries=max_retries-1)
        else:
            raise FetchError(f"Error fetching DM channels: {response.status_code} - {response.text}")

    def fetch_messages(self, channel_id: str, max_messages: int = 10000, user_id: Optional[str] = None) -> Generator[Dict[str, Any], None, None]:
        """
        Fetches messages from a channel, optionally filtering by author user_id.

        Args:
            channel_id (str): The ID of the channel to fetch messages from.
            max_messages (int): Maximum number of messages to fetch.
            user_id (str): If specified, only fetch messages authored by this user.

        Returns:
            List[Dict[str, Any]]: A list of message dictionaries.
        """
        url = f"{self.BASE_URL}/channels/{channel_id}/messages"
        fetched_count = 0
        last_message_id = None

        while fetched_count < max_messages:
            params = {"limit": 100}  # Max limit per request
            if last_message_id:
                params["before"] = last_message_id

            try:
                response = self.session.get(url, params=params)
            except Exception as e:
                self.logger.error("Request failed: %s", e)
                raise FetchError(f"Failed to fetch messages: {e}") from e

            if response.status_code == 429:
                retry_after = response.json().get("retry_after", 1) + random.randint(2, 5)  # Adding buffer
                self.logger.warning("Rate limit hit. Retrying after %s seconds.", retry_after)
                time.sleep(retry_after)
                continue

            if response.status_code != 200:
                raise FetchError(
                    f"Error fetching messages from channel {channel_id}: "
                    f"{response.status_code} - {response.text}"
                )

            batch = response.json()
            if not batch:
                self.logger.info("No more messages to fetch.")
                break  # No more messages

            for message in batch:
                if message.get("type") != 0:  # Skip non-default messages
                    continue

                if user_id and message.get("author", {}).get("id") != user_id:
                    continue

                yield {
                    "message_id": message["id"],
                    "timestamp": message["timestamp"],
                    "channel_id": channel_id
                }

                fetched_count += 1
                if fetched_count >= max_messages:
                    self.logger.info("Reached the maximum of %s messages.", max_messages)
                    break

            last_message_id = batch[-1]["id"]

            # Optional: Respectful delay between requests to avoid hitting rate limits
            time.sleep(0.2)

        self.logger.info("Fetched a total of %s messages from channel %s.", fetched_count, channel_id)

    def delete_message(self, channel_id: str, message_id: str, max_retries: int = 5) -> bool:
        """
        Deletes a single Discord message with retry logic for rate limits.

        Args:
            channel_id (str): ID of the channel containing the message.
            message_id (str): ID of the message to delete.
            max_retries (int): Maximum number of retries in case of rate limiting.

        Returns:
            bool: True if deletion was successful, False otherwise.
        """
        delete_url = f"{self.BASE_URL}/channels/{channel_id}/messages/{message_id}"
        retries = 0

        while retries < max_retries:
            response = self.session.delete(delete_url)

            if response.status_code == 204:
                self.logger.info("Deleted message %s in channel %s.", message_id, channel_id)
                return True
            elif response.status_code == 429:
                retry_after = response.json().get("retry_after", 1) + random.randint(25,35)  # Adding buffer
                self.logger.warning("Rate limited when deleting message %s. Retrying after %s seconds.", message_id, retry_after)
                time.sleep(retry_after)
                retries += 1
                continue
            elif response.status_code == 403:
                self.logger.error("Forbidden: Cannot delete message %s in channel %s. Check permissions.", message_id, channel_id)
                return False
            elif response.status_code == 404:
                self.logger.error("Not Found: Message %s or channel %s does not exist.", message_id, channel_id)
                return False
            else:
                self.logger.error("Failed to delete message %s in channel %s. Status Code: %s", message_id, channel_id, response.status_code)
                return False

        self.logger.error("Max retries exceeded for message %s in channel %s.", message_id, channel_id)
        return False

class MessageCleaner:
    def __init__(
        self,
        api: DiscordAPI,
        user_id: Optional[str] = None,
        include_ids: Optional[List[str]] = None,
        exclude_ids: Optional[List[str]] = None,
        time_delta: timedelta = timedelta(weeks=2)
    ):
        """
        Initializes the MessageCleaner.

        Args:
            api (DiscordAPI): An instance of DiscordAPI.
            user_id (str): The user ID whose messages will be targeted.
            include_ids (List[str], optional): List of channel/guild/parent IDs to include. Defaults to None.
            exclude_ids (List[str], optional): List of channel/guild/parent IDs to exclude. Defaults to None.
            time_delta (timedelta, optional): Time delta to determine message age. Defaults to two weeks.
        """
        self.api = api
        self._user_id = user_id
        self.include_ids = include_ids or []
        self.exclude_ids = exclude_ids or []
        self.time_delta = time_delta
        self.logger = logging.getLogger(self.__class__.__name__)

        assert not set(self.include_ids).intersection(self.exclude_ids), "Include and exclude IDs must be disjoint"

    @property
    def user_id(self) -> str:
        if self._user_id:
            return self._user_id
        elif DISCORD_USER_ID:
            return DISCORD_USER_ID
        elif user_id := os.getenv("DISCORD_USER_ID"):
            return user_id
        else:
            raise ValueError("No user ID found. Pass user_id as argument, set DISCORD_USER_ID variable, or set DISCORD_USER_ID environment variable.")

    def get_all_channels(self) -> List[Dict[str, Any]]:
        """
        Retrieves all relevant channels based on include and exclude IDs.

        Returns:
            List[Dict[str, Any]]: A list of channel dictionaries.
        """
        all_channels = []

        channel_types = {0: "GuildText", 1: "DM", 3: "GroupDM"} # https://discord-api-types.dev/api/discord-api-types-v9/enum/ChannelType
        guilds = self.api.get_guilds()
        guild_ids = [guild["id"] for guild in guilds]
        guild_channels = self.api.get_guild_channels_multiple(guild_ids)

        # Fetch root channels
        root_channels = self.api.get_root_channels()
        # Process root channels
        for channel in root_channels:
            if channel["type"] not in channel_types:
                self.logger.debug("Skipping unknown channel type: %s", channel['type'])
                continue
            channel_repr = channel_str(channel)
            if self.include_ids and channel["id"] not in self.include_ids:
                self.logger.debug("Excluding %s not in include_ids.", channel_repr)
                continue
            if self.exclude_ids and channel["id"] in self.exclude_ids:
                self.logger.debug("Excluding %s in exclude_ids.", channel_repr)
                continue
            all_channels.append(channel)
            self.logger.debug("Included %s.", channel_repr)
        # Process Guild channels
        for channel in guild_channels:
            if channel["type"] not in channel_types:
                self.logger.debug("Skipping unknown channel type: %s", channel['type'])
                continue
            channel_repr = channel_str(channel)
            if self.exclude_ids and (
                channel["id"] in self.exclude_ids
                or channel["guild_id"] in self.exclude_ids
                or (channel["parent_id"] and channel["parent_id"] in self.exclude_ids)
            ):
                self.logger.debug("Excluding %s in exclude_ids.", channel_repr)
                continue

            if self.include_ids and not (
                channel["id"] in self.include_ids
                or channel["guild_id"] in self.include_ids
                or (channel["parent_id"] and channel["parent_id"] in self.include_ids)
            ):
                self.logger.debug("Excluding %s not in include_ids.", channel_repr)
                continue

            all_channels.append(channel)
            self.logger.debug("Included %s.", channel_repr)

        self.logger.info("Total channels to process: %s", len(all_channels))
        return all_channels

    def fetch_all_messages(self, channel: Dict[str, Any]) -> Generator[Dict[str, Any], None, None]:
        """
        Fetches all messages from a given channel authored by the specified user.

        Args:
            channel (Dict[str, Any]): The channel dictionary.

        Returns:
            Generator[Dict[str, Any], None, None]: A list of message dictionaries.
        """
        channel_id = channel["id"]
        self.logger.info("Fetching messages from %s", channel_str(channel))
        fetched_count = 0
        for message in self.api.fetch_messages(channel_id, user_id=self.user_id):
            yield message
            fetched_count += 1
        self.logger.info("Fetched %s messages from %s.", fetched_count, channel_str(channel))

    def delete_messages_older_than(self, messages: Generator[Dict[str, Any], None, None], cutoff_time: datetime) -> tuple[int, int]:
        """
        Deletes messages older than the cutoff_time.

        Args:
            messages (List[Dict[str, Any]]): List of message dictionaries.
            cutoff_time (datetime): The cutoff datetime; messages older than this will be deleted.

        Returns:
            int: Number of messages deleted.
        """
        deleted_count = 0
        ignored_count = 0
        for message in messages:
            message_id = message["message_id"]
            timestamp_str = message["timestamp"]

            # Parse the timestamp string to a datetime object
            message_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))

            if message_time < cutoff_time:
                self.logger.info("Deleting message %s sent at %s UTC.", message_id, message_time.isoformat())
                success = self.api.delete_message(message["channel_id"], message_id)
                if success:
                    deleted_count += 1
                    time.sleep(random.uniform(1.5,2))  # Add a random delay between deletions
                else:
                    self.logger.warning("Failed to delete message %s in channel %s.", message_id, message['channel_id'])
            else:
                ignored_count += 1
                self.logger.debug("Message %s is recent. Skipping.", message_id)


        return deleted_count, ignored_count

    def clean_messages(self) -> int:
        """
        Main method to clean messages based on the specified criteria.

        Returns:
            int: Total number of messages deleted.
        """
        total_deleted = 0
        cutoff_time = datetime.now(timezone.utc) - self.time_delta
        self.logger.info("Deleting messages older than %s UTC.", cutoff_time.isoformat())

        channels = self.get_all_channels()
        for channel in channels:
            print(channel_str(channel))

        for channel in channels:
            # Add channel_id to each message for deletion
            channel_repr = channel_str(channel)
            deleted, ignored = self.delete_messages_older_than(self.fetch_all_messages(channel), cutoff_time)
            self.logger.info("Ignored %s recent messages in %s.", ignored, channel_repr)
            self.logger.info("Deleted %s messages from channel %s.", deleted, channel_repr)
            total_deleted += deleted

        self.logger.info("Total messages deleted: %s", total_deleted)
        return total_deleted

def parse_time_delta(time_str: str) -> timedelta:
    """
    Parses a time delta string and returns a timedelta object.

    Supported formats:
    - 'weeks=2'
    - 'days=10'
    - 'hours=5'
    - 'minutes=30'
    - Combinations like 'weeks=1,days=3'

    Args:
        time_str (str): The time delta string.

    Returns:
        timedelta: The corresponding timedelta object.

    Raises:
        argparse.ArgumentTypeError: If the format is incorrect.
    """
    try:
        kwargs = {}
        parts = time_str.split(',')
        for part in parts:
            value: str | int
            key, value = part.split('=')
            key = key.strip().lower()
            value = int(value.strip())
            if key not in ['weeks', 'days', 'hours', 'minutes', 'seconds']:
                raise ValueError
            kwargs[key] = value
        return timedelta(**kwargs)
    except Exception as e:
        raise argparse.ArgumentTypeError(f"Invalid time delta format: {time_str}. Use format like 'weeks=2,days=3'. Error: {e}")

def main():
    # Load environment variables from .env file
    # Parse command-line arguments
    load_dotenv()
    parser = argparse.ArgumentParser(description="Delete Discord messages older than a specified time delta.")
    parser.add_argument(
        "--include-ids",
        type=str,
        nargs='*',
        default=[],
        help="List of channel/guild/parent IDs to include."
    )
    parser.add_argument(
        "--exclude-ids",
        type=str,
        nargs='*',
        default=[],
        help="List of channel/guild/parent IDs to exclude."
    )
    parser.add_argument(
        "--time-delta",
        type=parse_time_delta,
        default=timedelta(weeks=2),
        help="Time delta for message deletion in format like 'weeks=2,days=3'. Default is 'weeks=2'."
    )
    args = parser.parse_args()

    include_ids = args.include_ids
    exclude_ids = args.exclude_ids
    time_delta = args.time_delta


    # Initialize DiscordAPI and MessageCleaner
    api = DiscordAPI(token=DISCORD_TOKEN)
    cleaner = MessageCleaner(
        api=api,
        include_ids=include_ids,
        exclude_ids=exclude_ids,
        time_delta=time_delta
    )
    # Start cleaning messages
    try:
        total_deleted = cleaner.clean_messages()
        logging.info("Script completed. Total messages deleted: %s", total_deleted)
    except FetchError as e:
        logging.error("FetchError occurred: %s", e)
    except Exception as e:
        logging.error("An unexpected error occurred: %s", e)

if __name__ == "__main__":
    main()
#https://discord.com/developers/docs/resources/channel
#https://discord-api-types.dev/api/discord-api-types-v9/enum/MessageType

