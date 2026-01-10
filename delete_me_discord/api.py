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
from .utils import AuthenticationError, format_timestamp, ReachedMaxRetries, ResourceUnavailable, UnexpectedStatus


class DiscordAPI:
    BASE_URL = "https://discord.com/api/v10"
    # By default we skip over unavailable resources (403/404) and malformed reactions,
    # logging a warning instead of aborting the entire run. This keeps long jobs moving
    # while still surfacing the issue to the user.

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

    def get_guilds(self) -> List[Dict[str, Any]]:
        """
        Fetches the list of guilds the user is part of.

        Returns:
            List[Dict[str, Any]]: List of guilds.

        Raises:
            AuthenticationError, ResourceUnavailable, ReachedMaxRetries, UnexpectedStatus
        """
        url = f"{self.BASE_URL}/users/@me/guilds"
        return self._request(url, description="fetch guilds")

    def get_guild_channels(self, guild_id: str) -> List[Dict[str, Any]]:
        """
        Fetches channels for a specific guild.

        Args:
            guild_id (str): The ID of the guild.

        Returns:
            List[Dict[str, Any]]: List of channels in the guild.

        Raises:
            AuthenticationError, ResourceUnavailable, ReachedMaxRetries, UnexpectedStatus
        """
        url = f"{self.BASE_URL}/guilds/{guild_id}/channels"
        return self._request(url, description=f"fetch channels for guild {guild_id}")

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
            except ResourceUnavailable as e:
                self.logger.warning("Skipping guild %s as it is unavailable. Error: %s", guild_id, str(e))
        return all_channels

    def get_root_channels(self) -> List[Dict[str, Any]]:
        """
        Fetches root (DM) channels.

        Returns:
            List[Dict[str, Any]]: List of root channels.

        Raises:
            AuthenticationError, ResourceUnavailable, ReachedMaxRetries, UnexpectedStatus
        """
        url = f"{self.BASE_URL}/users/@me/channels"
        return self._request(url, description="fetch root channels")

    def get_current_user(self) -> Dict[str, Any]:
        """
        Fetches the authenticated user's profile.

        Returns:
            Dict[str, Any]: User object for the authenticated token.

        Raises:
            AuthenticationError, ResourceUnavailable, ReachedMaxRetries, UnexpectedStatus
        """
        url = f"{self.BASE_URL}/users/@me"
        return self._request(url, description="fetch current user")[0]

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
        reached_cutoff = False

        while fetched_count < max_messages:
            params = {"limit": 100}
            if last_message_id:
                params["before"] = last_message_id

            try:
                response = self._request(url, description=f"fetch messages in channel {channel_id}", params=params)
            except ResourceUnavailable as e:
                self.logger.warning("Skipping channel %s (unavailable: %s).", channel_id, e)
                break
            
            if not response:
                self.logger.debug("No more messages to fetch in channel %s.", channel_id)
                break

            for message in response:
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
                    self.logger.debug("Reached the maximum of %s messages.", max_messages)
                    break

            if reached_cutoff or fetched_count >= max_messages:
                break

            last_message_id = response[-1]["id"]
            # Implement randomized sleep after each fetch
            sleep_time = random.uniform(*fetch_sleep_time_range)
            self.logger.debug("Sleeping for %.2f seconds after fetching messages.", sleep_time)
            time.sleep(sleep_time)  # Respectful delay between requests

        self.logger.debug("Fetched a total of %s messages from channel %s.", fetched_count, channel_id)

    def fetch_message_by_id(self, channel_id: str, message_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a single message by ID from a channel.

        Args:
            channel_id (str): The ID of the channel.
            message_id (str): The ID of the message.

        Returns:
            Optional[Dict[str, Any]]: Message data or None if not found/accessible.
        """
        url = f"{self.BASE_URL}/channels/{channel_id}/messages"
        try:
            response = self._request(url, description=f"fetch message {message_id} in channel {channel_id}", method="get", params={"around": message_id, "limit": 1})
        except ResourceUnavailable as e:
            self.logger.warning("Skipping channel %s (unavailable: %s).", channel_id, e)
            return None

        if not response:
            self.logger.debug("Message %s not found in channel %s.", message_id, channel_id)
            return None

        message = response[0]
        if message.get("id") != message_id:
            self.logger.warning(f"Message %s not found in channel %s.", message_id, channel_id)
            return None

        return {
                "message_id": message["id"],
                "timestamp": message["timestamp"],
                "channel_id": channel_id,
                "type": MessageType(message.get("type", 0)),
                "author_id": message.get("author", {}).get("id"),
                "reactions": message.get("reactions", []),
                }


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
        try:
            self._request(delete_url, description=f"delete message {message_id} in channel {channel_id}", method="delete")
        except ResourceUnavailable as e:
            self.logger.warning("Skipping deletion of message %s in channel %s (unavailable: %s).", message_id, channel_id, e)
            return False
        return True
        

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
            self.logger.warning("Skipping delete reaction: missing emoji identifier in payload: %s", emoji)
            return False

        encoded_identifier = urllib.parse.quote(emoji_identifier)
        delete_url = f"{self.BASE_URL}/channels/{channel_id}/messages/{message_id}/reactions/{encoded_identifier}/@me"
        try:
            self._request(delete_url, description=f"delete reaction {emoji_identifier} from message {message_id} in channel {channel_id}", method="delete")
        except ResourceUnavailable as e:
            self.logger.warning("Skipping deletion of reaction %s from message %s in channel %s (unavailable: %s).", emoji_identifier, message_id, channel_id, e)
            return False
        return True


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

    def _request(self, url: str, description: str, method: str = "get", params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Internal method to handle GET/DELETE requests with retry logic.

        Args:
            url (str): The endpoint URL.
            description (str): Description of the request for logging.
            method (str): HTTP method to use ("get" or "delete").
            params (Optional[Dict[str, Any]]): Query parameters for the request.

        Returns:
            List[Dict[str, Any]]: The JSON response data (empty list for successful DELETE).

        Raises:
            AuthenticationError: When the request is unauthorized (401).
            ResourceUnavailable: When the resource returns 403/404.
            UnexpectedStatus: For non-handled HTTP status codes.
            ReachedMaxRetries: When the request exceeds max retries.
        """
        assert method in {"get", "delete"}
        success_code = {"get": 200, "delete": 204}[method]
        attempts = 0
        while attempts <= self.max_retries:
            try:
                response = self.session.request(method=method, url=url, params=params)
            except requests.RequestException as exc:
                buffer = random.uniform(*self.retry_time_buffer)
                total_retry_after = 1 + buffer
                self.logger.warning(
                    "Network error while attempting to %s (%s). Retrying after %.2f seconds.",
                    description,
                    exc,
                    total_retry_after,
                )
                time.sleep(total_retry_after)
                attempts += 1
                continue

            if response.status_code in {200, 204}: # success
                if response.status_code != success_code:
                    raise UnexpectedStatus(
                        f"Unexpected status {response.status_code} for {method.upper()} while attempting to {description}."
                    )
                # DELETE 204 typically has no body; normalize to an empty list.
                if response.status_code == 204:
                    return []
                data = response.json()
                if not isinstance(data, list):
                    data = [data]
                return data
            elif response.status_code == 429 or 500 <= response.status_code < 600: # retry
                try:
                    retry_after = response.json().get("retry_after", 1)
                except Exception:
                    retry_after = 1
                buffer = random.uniform(*self.retry_time_buffer)
                total_retry_after = retry_after + buffer
                self.logger.warning("Rate limit hit while attempting to %s. Retrying after %.2f seconds.", description, total_retry_after)
                time.sleep(total_retry_after)
                attempts += 1
            elif response.status_code == 401: # unauthorized
                raise AuthenticationError(f"Unauthorized while attempting to {description}. Status Code: 401")
            elif response.status_code in {403, 404}: # unavailable
                raise ResourceUnavailable(f"Resource unavailable while attempting to {description}. Status Code: {response.status_code}")
            else: # unhandled
                raise UnexpectedStatus(f"Unhandled status code {response.status_code} while attempting to {description}.")

        raise ReachedMaxRetries(f"Max retries exceeded while attempting to {description}.")
