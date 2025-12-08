# delete_me_discord/api.py

import os
import time
import random
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Generator, Union
import logging
import requests
import urllib.parse
from .type_enums import MessageType
from .utils import FetchError, format_timestamp


class DiscordAPI:
    BASE_URL = "https://discord.com/api/v10"

    def __init__(
        self,
        token: Optional[str] = None,
        max_retries: int = 5,
        retry_time_buffer: Tuple[float, float] = (1.0, 1.0)
    ):
        """
        Initializes the DiscordAPI instance.

        Args:
            token (Optional[str]): Discord authentication token.
            max_retries (int): Maximum number of retry attempts for rate limiting.
            retry_time_buffer (Tuple[float, float]): Range of additional time to wait after rate limit responses.

        Raises:
            ValueError: If the Discord token is not provided.
        """
        self._token = token or os.getenv("DISCORD_TOKEN")
        if not self._token:
            raise ValueError("Discord token not provided. Set DISCORD_TOKEN environment variable or pass as an argument.")

        self.max_retries = max_retries
        self.retry_time_buffer = retry_time_buffer  # (min_buffer, max_buffer)

        self.headers = {
            "Authorization": self._token,
            "Content-Type": "application/json"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.logger = logging.getLogger(self.__class__.__name__)

    def get_guilds(self, max_retries: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetches the list of guilds the user is part of.

        Args:
            max_retries (Optional[int]): Overrides the instance's max_retries if provided.

        Returns:
            List[Dict[str, Any]]: List of guilds.

        Raises:
            FetchError: If unable to fetch guilds after retries.
        """
        url = f"{self.BASE_URL}/users/@me/guilds"
        return self._get_request(url, max_retries=max_retries, description="fetch guilds")

    def get_guild_channels(self, guild_id: str, max_retries: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetches channels for a specific guild.

        Args:
            guild_id (str): The ID of the guild.
            max_retries (Optional[int]): Overrides the instance's max_retries if provided.

        Returns:
            List[Dict[str, Any]]: List of channels in the guild.

        Raises:
            FetchError: If unable to fetch channels after retries.
        """
        url = f"{self.BASE_URL}/guilds/{guild_id}/channels"
        return self._get_request(url, max_retries=max_retries, description=f"fetch channels for guild {guild_id}")

    def get_guild_channels_multiple(self, guild_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Fetches channels for multiple guilds.

        Args:
            guild_ids (List[str]): List of guild IDs.

        Returns:
            List[Dict[str, Any]]: Aggregated list of channels from all guilds.
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

    def get_root_channels(self, max_retries: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetches root (DM) channels.

        Args:
            max_retries (Optional[int]): Overrides the instance's max_retries if provided.

        Returns:
            List[Dict[str, Any]]: List of root channels.

        Raises:
            FetchError: If unable to fetch root channels after retries.
        """
        url = f"{self.BASE_URL}/users/@me/channels"
        return self._get_request(url, max_retries=max_retries, description="fetch root channels")

    def get_current_user(self, max_retries: Optional[int] = None) -> Dict[str, Any]:
        """
        Fetches the authenticated user's profile.

        Args:
            max_retries (Optional[int]): Overrides the instance's max_retries if provided.

        Returns:
            Dict[str, Any]: User object for the authenticated token.

        Raises:
            FetchError: If unable to fetch the user after retries.
        """
        url = f"{self.BASE_URL}/users/@me"
        return self._get_request(url, max_retries=max_retries, description="fetch current user")

    def fetch_messages(
        self,
        channel_id: str,
        max_messages: Union[int, float] = float("inf"),
        fetch_sleep_time_range: Tuple[float, float] = (0.2, 0.2),
        fetch_since: Optional[datetime] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Fetches messages from a channel, optionally filtering by author.

        Args:
            channel_id (str): The ID of the channel.
            max_messages (int): Maximum number of messages to fetch.
            fetch_sleep_time_range (Tuple[float, float]): Range for sleep time between fetch requests.
            fetch_since (Optional[datetime]): Only fetch messages newer than this timestamp.

        Yields:
            Dict[str, Any]: Message data.
        """
        url = f"{self.BASE_URL}/channels/{channel_id}/messages"
        fetched_count = 0
        last_message_id = None
        retries = 0
        reached_cutoff = False

        while fetched_count < max_messages:
            params = {"limit": 100}
            if last_message_id:
                params["before"] = last_message_id

            try:
                response = self.session.get(url, params=params)
            except requests.RequestException as e:
                self.logger.error("Request failed: %s", e)
                raise FetchError(f"Failed to fetch messages: {e}") from e

            if response.status_code == 429:
                retry_after = response.json().get("retry_after", 1)
                buffer = random.uniform(*self.retry_time_buffer)
                total_retry_after = retry_after + buffer
                self.logger.warning("Rate limit hit. Retrying after %.2f seconds.", total_retry_after)
                time.sleep(total_retry_after)
                retries += 1
                if retries >= self.max_retries:
                    raise FetchError("Max retries exceeded while fetching messages.")
                continue
            else:
                retries = 0  # Reset retries on successful response or non-429 error

            if response.status_code != 200:
                if response.status_code == 403:
                    error_code = None
                    try:
                        error_code = response.json().get("code")
                    except Exception:
                        error_code = None

                    if error_code in {50001, 50013}:
                        self.logger.warning(
                            "Skipping channel %s due to missing access (code %s). Status Code: %s - %s",
                            channel_id,
                            error_code,
                            response.status_code,
                            response.text
                        )
                        break

                raise FetchError(
                    f"Error fetching messages from channel {channel_id}: "
                    f"{response.status_code} - {response.text}"
                )

            batch = response.json()
            if not batch:
                self.logger.info("No more messages to fetch in channel %s.", channel_id)
                break

            for message in batch:
                message_time = datetime.fromisoformat(message["timestamp"].replace('Z', '+00:00'))
                if fetch_since and message_time < fetch_since:
                    reached_cutoff = True
                    self.logger.debug(
                        "Reached fetch cutoff (%s) in channel %s.",
                        format_timestamp(fetch_since),
                        channel_id
                    )
                    break
                yield {
                    "message_id": message["id"],
                    "timestamp": message["timestamp"],
                    "channel_id": channel_id,
                    "type": MessageType(message.get("type", 0)),
                    "author_id": message.get("author", {}).get("id"),
                    "reactions": message.get("reactions", []),
                }

                fetched_count += 1
                if fetched_count >= max_messages:
                    self.logger.info("Reached the maximum of %s messages.", max_messages)
                    break

            if reached_cutoff or fetched_count >= max_messages:
                break

            last_message_id = batch[-1]["id"]
            # Implement randomized sleep after each fetch
            sleep_time = random.uniform(*fetch_sleep_time_range)
            self.logger.debug("Sleeping for %.2f seconds after fetching messages.", sleep_time)
            time.sleep(sleep_time)  # Respectful delay between requests

        self.logger.info("Fetched a total of %s messages from channel %s.", fetched_count, channel_id)


    def delete_message(
        self,
        channel_id: str,
        message_id: str,
    ) -> bool:
        """
        Deletes a specific message with retry logic for rate limiting.

        Args:
            channel_id (str): ID of the channel containing the message.
            message_id (str): ID of the message to delete.

        Returns:
            bool: True if deletion was successful, False otherwise.
        """
        delete_url = f"{self.BASE_URL}/channels/{channel_id}/messages/{message_id}"
        retries = 0

        while retries < self.max_retries:
            try:
                response = self.session.delete(delete_url)
            except requests.RequestException as e:
                self.logger.error("Failed to delete message %s: %s", message_id, e)
                return False

            if response.status_code == 204:
                self.logger.info("Deleted message %s in channel %s.", message_id, channel_id)
                return True
            elif response.status_code == 429:
                retry_after = response.json().get("retry_after", 1)
                buffer = random.uniform(*self.retry_time_buffer)
                total_retry_after = retry_after + buffer
                self.logger.warning(
                    "Rate limited when deleting message %s in channel %s. Retrying after %.2f seconds.",
                    message_id, channel_id, total_retry_after
                )
                time.sleep(total_retry_after)
                retries += 1
                continue
            elif response.status_code in {403, 404}:
                self.logger.error(
                    "Cannot delete message %s in channel %s. Status Code: %s",
                    message_id, channel_id, response.status_code
                )
                return False
            else:
                self.logger.error(
                    "Failed to delete message %s in channel %s. Status Code: %s",
                    message_id, channel_id, response.status_code
                )
                return False

        self.logger.error("Max retries exceeded for deleting message %s in channel %s.", message_id, channel_id)
        return False

    def delete_own_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: Dict[str, Any]
    ) -> bool:
        """
        Deletes the authenticated user's reaction from a message.

        Args:
            channel_id (str): ID of the channel containing the message.
            message_id (str): ID of the message.
            emoji (Dict[str, Any]): Emoji dict from the message reaction object.

        Returns:
            bool: True if deletion was successful, False otherwise.
        """
        emoji_identifier = self._format_emoji_identifier(emoji)
        if not emoji_identifier:
            self.logger.debug("Could not format emoji identifier for reaction: %s", emoji)
            return False

        encoded_identifier = urllib.parse.quote(emoji_identifier)
        delete_url = f"{self.BASE_URL}/channels/{channel_id}/messages/{message_id}/reactions/{encoded_identifier}/@me"

        retries = 0
        while retries < self.max_retries:
            try:
                response = self.session.delete(delete_url)
            except requests.RequestException as e:
                self.logger.error("Failed to delete reaction %s on message %s: %s", emoji_identifier, message_id, e)
                return False

            if response.status_code == 204:
                self.logger.info("Deleted reaction %s on message %s in channel %s.", emoji_identifier, message_id, channel_id)
                return True
            elif response.status_code == 429:
                retry_after = response.json().get("retry_after", 1)
                buffer = random.uniform(*self.retry_time_buffer)
                total_retry_after = retry_after + buffer
                self.logger.warning(
                    "Rate limited when deleting reaction %s on message %s. Retrying after %.2f seconds.",
                    emoji_identifier, message_id, total_retry_after
                )
                time.sleep(total_retry_after)
                retries += 1
                continue
            elif response.status_code in {403, 404}:
                self.logger.error(
                    "Cannot delete reaction %s on message %s. Status Code: %s",
                    emoji_identifier, message_id, response.status_code
                )
                return False
            else:
                self.logger.error(
                    "Failed to delete reaction %s on message %s. Status Code: %s",
                    emoji_identifier, message_id, response.status_code
                )
                return False

        self.logger.error(
            "Max retries exceeded for deleting reaction %s on message %s in channel %s.",
            emoji_identifier, message_id, channel_id
        )
        return False

    def _format_emoji_identifier(self, emoji: Dict[str, Any]) -> Optional[str]:
        """
        Formats an emoji dict into the identifier string required by the Discord API.

        Args:
            emoji (Dict[str, Any]): Emoji dictionary containing 'name' and optionally 'id'.

        Returns:
            Optional[str]: The formatted emoji identifier or None if insufficient data.
        """
        if not emoji:
            return None
        name = emoji.get("name")
        emoji_id = emoji.get("id")
        if emoji_id:
            return f"{name}:{emoji_id}"
        return name

    def _get_request(self, url: str, max_retries: Optional[int], description: str) -> List[Dict[str, Any]]:
        """
        Internal method to handle GET requests with retry logic.

        Args:
            url (str): The endpoint URL.
            max_retries (Optional[int]): Overrides the instance's max_retries if provided.
            description (str): Description of the request for logging.

        Returns:
            List[Dict[str, Any]]: The JSON response data.

        Raises:
            FetchError: If the request fails after maximum retries.
        """
        attempts = 0
        effective_max_retries = max_retries if max_retries is not None else self.max_retries

        while attempts < effective_max_retries:
            try:
                response = self.session.get(url)
            except requests.RequestException as e:
                self.logger.error("Request failed: %s", e)
                raise FetchError(f"Failed to {description}: {e}") from e

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                retry_after = response.json().get("retry_after", 1)
                buffer = random.uniform(*self.retry_time_buffer)
                total_retry_after = retry_after + buffer
                self.logger.warning("Rate limit hit while attempting to %s. Retrying after %.2f seconds.", description, total_retry_after)
                time.sleep(total_retry_after)
                attempts += 1
            else:
                raise FetchError(
                    f"Error {description}: {response.status_code} - {response.text}"
                )

        raise FetchError(f"Max retries exceeded for {description}.")
