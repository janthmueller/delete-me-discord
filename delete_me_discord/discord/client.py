"""Discord endpoint operations over a reusable HTTP transport."""

import urllib.parse
from datetime import datetime
from typing import Any, Dict, Generator, List, Mapping, Optional, Tuple, Union, cast

from .models import (
    DeleteOutcome,
    DiscordChannel,
    DiscordEmoji,
    DiscordMessage,
    UpdateOutcome,
)
from .rate_limits import (
    DELETE_POLICY,
    FETCH_POLICY,
    THREAD_SEARCH_POLICY,
    DiscordRequestScheduler,
    WaitSnapshot,
)
from .type_enums import MessageType, ReactionType
from .errors import ResourceUnavailable, UnexpectedStatus
from .transport import DiscordTransport
from ..logging import get_logger
from ..utils import format_timestamp
from ..privacy import sensitive


class DiscordClient:
    BASE_URL = "https://discord.com/api/v10"
    # By default we skip over unavailable resources (403/404) and malformed reactions,
    # logging a warning instead of aborting the entire run. This keeps long jobs moving
    # while still surfacing the issue to the user.

    def __init__(
        self,
        token: Optional[str] = None,
        max_retries: int = 5,
        retry_time_buffer: Tuple[float, float] = (0.1, 0.3),
        request_timeout: Tuple[float, float] = (10.0, 30.0),
        request_intervals: Optional[Mapping[str, Tuple[float, float]]] = None,
        request_scheduler: Optional[DiscordRequestScheduler] = None,
        transport: Optional[DiscordTransport] = None,
    ):
        """
        Initialize endpoint operations over a Discord transport.

        Args:
            token (Optional[str]): Discord authentication token.
            max_retries (int): Maximum number of retry attempts for rate limiting.
            retry_time_buffer (Tuple[float, float]): Jitter added to retry delays.
            request_timeout (Tuple[float, float]): Connect and read timeouts.
            request_intervals (Optional[Mapping[str, Tuple[float, float]]]): Policy overrides.
            request_scheduler (Optional[DiscordRequestScheduler]): Optional scheduler override.

        Raises:
            ValueError: If the Discord token is not provided.
        """
        self.transport = transport or DiscordTransport(
            token=token,
            max_retries=max_retries,
            retry_time_buffer=retry_time_buffer,
            request_timeout=request_timeout,
            request_intervals=request_intervals,
            request_scheduler=request_scheduler,
        )
        self.logger = get_logger(self.__class__.__name__)
        self._last_fetch_summaries: Dict[str, Dict[str, Any]] = {}

    def configure_request_policy(
        self,
        name: str,
        interval: Tuple[float, float],
    ) -> None:
        """Set the minimum interval applied before consecutive requests in a policy."""
        self.transport.configure_policy(name, interval)

    def request_wait_snapshot(self, policy: str) -> WaitSnapshot:
        """Return cumulative scheduler waits for one request policy."""
        return self.transport.wait_snapshot(policy)

    def close(self) -> None:
        """Close the underlying Discord transport."""
        self.transport.close()

    def get_guilds(self) -> List[Dict[str, Any]]:
        """
        Fetches the list of guilds the user is part of.

        Returns:
            List[Dict[str, Any]]: List of guilds.

        Raises:
            AuthenticationError, ResourceUnavailable, ReachedMaxRetries, UnexpectedStatus
        """
        url = f"{self.BASE_URL}/users/@me/guilds"
        return self.transport.request(url, description="fetch guilds")

    def get_guild_channels(self, guild_id: str) -> List[DiscordChannel]:
        """
        Fetches channels for a specific guild.

        Args:
            guild_id (str): The ID of the guild.

        Returns:
            List[DiscordChannel]: List of channels in the guild.

        Raises:
            AuthenticationError, ResourceUnavailable, ReachedMaxRetries, UnexpectedStatus
        """
        url = f"{self.BASE_URL}/guilds/{guild_id}/channels"
        return cast(
            List[DiscordChannel],
            self.transport.request(
                url,
                description=f"fetch channels for guild {sensitive(guild_id)}",
            ),
        )

    def get_channel(self, channel_id: str) -> DiscordChannel:
        """Fetch one guild channel, category, thread, or private channel by exact ID."""
        url = f"{self.BASE_URL}/channels/{channel_id}"
        payload = self.transport.request(
            url,
            description=f"fetch channel {sensitive(channel_id)}",
        )
        if len(payload) != 1 or not isinstance(payload[0], dict):
            raise UnexpectedStatus(
                f"Malformed channel response while attempting to fetch channel {sensitive(channel_id)}."
            )
        return cast(DiscordChannel, payload[0])

    def get_guild_channels_multiple(self, guild_ids: List[str]) -> List[DiscordChannel]:
        """
        Fetches channels for multiple guilds.

        Args:
            guild_ids (List[str]): List of guild IDs.

        Returns:
            List[DiscordChannel]: Aggregated list of channels from all guilds.
        """
        all_channels = []
        for guild_id in guild_ids:
            try:
                channels = self.get_guild_channels(guild_id)
                all_channels.extend(channels)
                self.logger.diagnostic("Fetched %s channels from guild %s.", len(channels), sensitive(guild_id))
            except ResourceUnavailable as e:
                self.logger.warning("Skipping guild %s as it is unavailable. Error: %s", sensitive(guild_id), str(e))
        return all_channels

    def search_channel_threads(
        self,
        channel_id: str,
        *,
        include_archived: bool = False,
    ) -> List[DiscordChannel]:
        """Fetch accessible threads under one parent channel using the user API."""
        url = f"{self.BASE_URL}/channels/{channel_id}/threads/search"
        description = f"search threads for channel {sensitive(channel_id)}"
        threads: List[DiscordChannel] = []
        seen_ids: set[str] = set()
        params: Dict[str, Any] = {
            "limit": 25,
            "sort_by": "creation_time",
            "sort_order": "desc",
        }
        if not include_archived:
            params["archived"] = "false"
        previous_cursor = None

        while True:
            response = self.transport.request(
                url,
                description=description,
                params=params,
                pacing_policy=THREAD_SEARCH_POLICY,
            )
            payload = self._thread_collection_payload(response, description)
            page_threads = payload["threads"]
            for thread in page_threads:
                thread_id = thread.get("id")
                if thread_id is None or str(thread_id) in seen_ids:
                    continue
                seen_ids.add(str(thread_id))
                threads.append(thread)

            if not payload["has_more"]:
                break
            if not page_threads:
                self.logger.warning("Stopping %s pagination because Discord returned an empty page.", description)
                break

            cursor = page_threads[-1].get("id")
            if cursor is None or cursor == previous_cursor:
                self.logger.warning("Stopping %s pagination because the cursor did not advance.", description)
                break
            params["max_id"] = cursor
            previous_cursor = cursor

        return threads

    @staticmethod
    def _thread_collection_payload(response: List[Dict[str, Any]], description: str) -> Dict[str, Any]:
        if len(response) != 1 or not isinstance(response[0], dict):
            raise UnexpectedStatus(f"Malformed response while attempting to {description}.")
        payload = response[0]
        threads = payload.get("threads")
        if not isinstance(threads, list):
            raise UnexpectedStatus(f"Malformed thread list while attempting to {description}.")
        return {
            "threads": threads,
            "has_more": payload.get("has_more") is True,
        }

    def get_root_channels(self) -> List[DiscordChannel]:
        """
        Fetches root (DM) channels.

        Returns:
            List[DiscordChannel]: List of root channels.

        Raises:
            AuthenticationError, ResourceUnavailable, ReachedMaxRetries, UnexpectedStatus
        """
        url = f"{self.BASE_URL}/users/@me/channels"
        return cast(
            List[DiscordChannel],
            self.transport.request(url, description="fetch root channels"),
        )

    def get_current_user(self) -> Dict[str, Any]:
        """
        Fetches the authenticated user's profile.

        Returns:
            Dict[str, Any]: User object for the authenticated token.

        Raises:
            AuthenticationError, ResourceUnavailable, ReachedMaxRetries, UnexpectedStatus
        """
        url = f"{self.BASE_URL}/users/@me"
        return self.transport.request(url, description="fetch current user")[0]

    def get_current_guild_member(self, guild_id: str) -> Dict[str, Any]:
        """Fetch the authenticated user's member object for one guild."""
        url = f"{self.BASE_URL}/users/@me/guilds/{guild_id}/member"
        payload = self.transport.request(
            url,
            description=f"fetch current member for guild {sensitive(guild_id)}",
        )
        if len(payload) != 1 or not isinstance(payload[0], dict):
            raise UnexpectedStatus(
                "Malformed current-member response while attempting to fetch "
                f"current member for guild {sensitive(guild_id)}."
            )
        return payload[0]

    def fetch_messages(
        self,
        channel_id: str,
        max_messages: Union[int, float] = float("inf"),
        fetch_sleep_time_range: Tuple[float, float] = (0.2, 0.4),
        fetch_since: Optional[datetime] = None
    ) -> Generator[DiscordMessage, None, None]:
        """
        Fetch messages from a channel in newest-to-oldest order.

        Args:
            channel_id (str): The ID of the channel.
            max_messages (int): Maximum number of messages to fetch.
            fetch_sleep_time_range (Tuple[float, float]): Minimum interval between fetch requests.
            fetch_since (Optional[datetime]): Only fetch messages newer than this timestamp.

        Yields:
            DiscordMessage: Normalized message payload.
        """
        url = f"{self.BASE_URL}/channels/{channel_id}/messages"
        fetched_count = 0
        last_message_id = None
        fetch_stop_reason = "exhausted channel history"
        fetch_complete = fetch_since is None and max_messages == float("inf")
        self.configure_request_policy(FETCH_POLICY, fetch_sleep_time_range)
        wait_before = self.request_wait_snapshot(FETCH_POLICY)

        while fetched_count < max_messages:
            params = {"limit": 100}
            if last_message_id:
                params["before"] = last_message_id

            try:
                response = self.transport.request(
                    url,
                    description=f"fetch messages in channel {sensitive(channel_id)}",
                    params=params,
                    pacing_policy=FETCH_POLICY,
                )
            except ResourceUnavailable as e:
                self.logger.warning("Skipping channel %s (unavailable: %s).", sensitive(channel_id), e)
                fetch_complete = False
                break
            
            if not response:
                break

            for message in response:
                message_time = datetime.fromisoformat(message["timestamp"].replace('Z', '+00:00'))
                if fetch_since and message_time < fetch_since:
                    fetch_stop_reason = f"reached fetch cutoff ({format_timestamp(fetch_since)})"
                    break
                yield DiscordMessage(
                    message_id=message["id"],
                    timestamp=message["timestamp"],
                    channel_id=channel_id,
                    type=MessageType(message.get("type", 0)),
                    author_id=message.get("author", {}).get("id"),
                    author_username=message.get("author", {}).get("username"),
                    content=message.get("content"),
                    reactions=message.get("reactions", []),
                )

                fetched_count += 1
                if fetched_count >= max_messages:
                    fetch_stop_reason = f"reached message limit ({max_messages})"
                    break

            if fetch_stop_reason != "exhausted channel history" or fetched_count >= max_messages:
                break

            last_message_id = response[-1]["id"]

        wait_after = self.request_wait_snapshot(FETCH_POLICY)
        self._last_fetch_summaries[channel_id] = {
            "fetched_count": fetched_count,
            "stop_reason": fetch_stop_reason,
            "wait_count": wait_after.count - wait_before.count,
            "waited_seconds": wait_after.seconds - wait_before.seconds,
            "complete": fetch_complete,
        }

    def get_last_fetch_summary(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Return the last fetch summary recorded for a channel during this run."""
        return self._last_fetch_summaries.get(channel_id)

    def fetch_message_by_id(self, channel_id: str, message_id: str) -> Optional[DiscordMessage]:
        """
        Fetch a single message by ID from a channel.

        Args:
            channel_id (str): The ID of the channel.
            message_id (str): The ID of the message.

        Returns:
            Optional[DiscordMessage]: Normalized message payload or None if not found/accessible.
        """
        url = f"{self.BASE_URL}/channels/{channel_id}/messages"
        try:
            response = self.transport.request(
                url,
                description=f"fetch message {sensitive(message_id)} in channel {sensitive(channel_id)}",
                method="get",
                params={"around": message_id, "limit": 1},
                pacing_policy=FETCH_POLICY,
            )
        except ResourceUnavailable as e:
            self.logger.warning("Skipping channel %s (unavailable: %s).", sensitive(channel_id), e)
            return None

        if not response:
            self.logger.diagnostic("Message %s not found in channel %s.", sensitive(message_id), sensitive(channel_id))
            return None

        message = response[0]
        if message.get("id") != message_id:
            self.logger.warning("Message %s not found in channel %s.", sensitive(message_id), sensitive(channel_id))
            return None

        return DiscordMessage(
            message_id=message["id"],
            timestamp=message["timestamp"],
            channel_id=channel_id,
            type=MessageType(message.get("type", 0)),
            author_id=message.get("author", {}).get("id"),
            author_username=message.get("author", {}).get("username"),
            content=message.get("content"),
            reactions=message.get("reactions", []),
        )


    def delete_message(
        self,
        channel_id: str,
        message_id: str,
    ) -> DeleteOutcome:
        """
        Deletes a specific message with retry logic for rate limiting.

        Args:
            channel_id (str): ID of the channel containing the message.
            message_id (str): ID of the message to delete.

        Returns:
            DeleteOutcome: Whether the message was deleted, absent, or failed.
        """
        delete_url = f"{self.BASE_URL}/channels/{channel_id}/messages/{message_id}"
        try:
            self.transport.request(
                delete_url,
                description=f"delete message {sensitive(message_id)} in channel {sensitive(channel_id)}",
                method="delete",
                pacing_policy=DELETE_POLICY,
            )
        except ResourceUnavailable as e:
            if e.status_code == 404:
                self.logger.diagnostic(
                    "Message %s in channel %s is absent.",
                    sensitive(message_id),
                    sensitive(channel_id),
                )
                return DeleteOutcome.ABSENT
            self.logger.warning(
                "Skipping deletion of message %s in channel %s (unavailable: %s).",
                sensitive(message_id),
                sensitive(channel_id),
                e,
            )
            return DeleteOutcome.FAILED
        except UnexpectedStatus as e:
            if e.status_code != 400:
                raise
            if e.discord_code == 50083:
                self.logger.diagnostic(
                    "Thread %s was archived while deleting message %s.",
                    sensitive(channel_id),
                    sensitive(message_id),
                )
                return DeleteOutcome.THREAD_ARCHIVED
            self.logger.warning(
                "Discord rejected deletion of message %s in channel %s "
                "(HTTP 400, Discord code %s).",
                sensitive(message_id),
                sensitive(channel_id),
                e.discord_code if e.discord_code is not None else "unknown",
            )
            return DeleteOutcome.FAILED
        return DeleteOutcome.DELETED

    def delete_thread(self, thread_id: str) -> DeleteOutcome:
        """Delete one thread channel when Discord grants the required permission."""
        delete_url = f"{self.BASE_URL}/channels/{thread_id}"
        try:
            self.transport.request(
                delete_url,
                description=f"delete thread {sensitive(thread_id)}",
                method="delete",
                pacing_policy=DELETE_POLICY,
                expected_statuses={200, 204},
            )
        except ResourceUnavailable as e:
            if e.status_code == 404:
                self.logger.diagnostic(
                    "Thread %s is absent.",
                    sensitive(thread_id),
                )
                return DeleteOutcome.ABSENT
            self.logger.warning(
                "Skipping deletion of thread %s (unavailable or missing MANAGE_THREADS: %s).",
                sensitive(thread_id),
                e,
            )
            return DeleteOutcome.FAILED
        except UnexpectedStatus as e:
            if e.status_code != 400:
                raise
            self.logger.warning(
                "Discord rejected deletion of thread %s "
                "(HTTP 400, Discord code %s).",
                sensitive(thread_id),
                e.discord_code if e.discord_code is not None else "unknown",
            )
            return DeleteOutcome.FAILED
        return DeleteOutcome.DELETED

    def set_thread_archived(
        self,
        thread_id: str,
        *,
        archived: bool,
    ) -> UpdateOutcome:
        """Set one thread's archive state using Discord's idempotent channel update."""
        update_url = f"{self.BASE_URL}/channels/{thread_id}"
        state = "archived" if archived else "active"
        try:
            self.transport.request(
                update_url,
                description=(
                    f"set thread {sensitive(thread_id)} to {state} state"
                ),
                method="patch",
                json_body={"archived": archived},
                pacing_policy=DELETE_POLICY,
                expected_statuses={200},
            )
        except ResourceUnavailable as exc:
            if exc.status_code == 404:
                self.logger.diagnostic(
                    "Thread %s is absent while setting archive state.",
                    sensitive(thread_id),
                )
                return UpdateOutcome.ABSENT
            self.logger.warning(
                "Discord did not allow thread %s to enter %s state: %s",
                sensitive(thread_id),
                state,
                exc,
            )
            return UpdateOutcome.FAILED
        except UnexpectedStatus as exc:
            if exc.status_code != 400:
                raise
            self.logger.warning(
                "Discord rejected setting thread %s to %s state "
                "(HTTP 400, Discord code %s).",
                sensitive(thread_id),
                state,
                exc.discord_code if exc.discord_code is not None else "unknown",
            )
            return UpdateOutcome.FAILED
        return UpdateOutcome.APPLIED

    def delete_own_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: DiscordEmoji,
        reaction_type: ReactionType = ReactionType.NORMAL,
    ) -> DeleteOutcome:
        """
        Deletes the authenticated user's reaction from a message.

        Args:
            channel_id (str): ID of the channel containing the message.
            message_id (str): ID of the message.
            emoji (DiscordEmoji): Emoji dict from the message reaction object.
            reaction_type (ReactionType): Normal or burst (Super Reaction).

        Returns:
            DeleteOutcome: Whether the reaction was deleted, absent, or failed.
        """
        emoji_identifier = self._format_emoji_identifier(emoji)
        if not emoji_identifier:
            self.logger.warning(
                "Skipping delete reaction: missing emoji identifier in payload %s.",
                sensitive(emoji, full=True),
            )
            return DeleteOutcome.FAILED

        encoded_identifier = urllib.parse.quote(emoji_identifier)
        try:
            normalized_reaction_type = ReactionType(reaction_type)
        except (TypeError, ValueError):
            self.logger.warning("Skipping delete reaction: unsupported reaction type %r.", reaction_type)
            return DeleteOutcome.FAILED

        delete_url = f"{self.BASE_URL}/channels/{channel_id}/messages/{message_id}/reactions/{encoded_identifier}/@me"
        params = None
        if normalized_reaction_type == ReactionType.BURST:
            delete_url = f"{delete_url}/{int(normalized_reaction_type)}"
            params = {"burst": True}
        try:
            self.transport.request(
                delete_url,
                description=(
                    f"delete reaction {sensitive(emoji_identifier, full=True)} from message {sensitive(message_id)} "
                    f"in channel {sensitive(channel_id)}"
                ),
                method="delete",
                params=params,
                pacing_policy=DELETE_POLICY,
            )
        except ResourceUnavailable as e:
            if e.status_code == 404:
                self.logger.diagnostic(
                    "Reaction %s on message %s in channel %s is absent.",
                    sensitive(emoji_identifier, full=True),
                    sensitive(message_id),
                    sensitive(channel_id),
                )
                return DeleteOutcome.ABSENT
            self.logger.warning(
                "Skipping deletion of reaction %s from message %s in channel %s (unavailable: %s).",
                sensitive(emoji_identifier, full=True),
                sensitive(message_id),
                sensitive(channel_id),
                e,
            )
            return DeleteOutcome.FAILED
        except UnexpectedStatus as e:
            if e.status_code != 400:
                raise
            if e.discord_code == 50083:
                self.logger.diagnostic(
                    "Thread %s was archived while deleting reaction %s from message %s.",
                    sensitive(channel_id),
                    sensitive(emoji_identifier, full=True),
                    sensitive(message_id),
                )
                return DeleteOutcome.THREAD_ARCHIVED
            self.logger.warning(
                "Discord rejected deletion of reaction %s from message %s in "
                "channel %s (HTTP 400, Discord code %s).",
                sensitive(emoji_identifier, full=True),
                sensitive(message_id),
                sensitive(channel_id),
                e.discord_code if e.discord_code is not None else "unknown",
            )
            return DeleteOutcome.FAILED
        return DeleteOutcome.DELETED


    def _format_emoji_identifier(self, emoji: DiscordEmoji) -> Optional[str]:
        """
        Formats an emoji dict into the identifier string required by the Discord API.

        Args:
            emoji (DiscordEmoji): Emoji dictionary containing 'name' and optionally 'id'.

        Returns:
            Optional[str]: The formatted emoji identifier or None if insufficient data.
        """
        if not emoji:
            return None
        name = emoji.get("name")
        emoji_id = emoji.get("id")
        if emoji_id:
            return f"{'null' if name is None else name}:{emoji_id}"
        return name
