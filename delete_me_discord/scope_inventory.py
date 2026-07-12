from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Generator

from .api import DiscordAPI
from .channel_types import GUILD_CLEANUP_CHANNEL_TYPES, ROOT_MESSAGE_CHANNEL_TYPES
from .models import DiscordChannel
from .privacy import sensitive
from .scope_filter import ScopeFilter, ThreadDiscoveryMode
from .utils import ResourceUnavailable


@dataclass(frozen=True)
class ScopeDiscoverySeed:
    """Reusable top-level discovery data and an optional safe guild allowlist."""

    guilds: tuple[dict[str, Any], ...]
    root_channels: tuple[DiscordChannel, ...]
    guild_ids: frozenset[str] | None = None


@dataclass(frozen=True)
class ScopeInventory:
    guilds: list[dict[str, Any]]
    root_channels: list[DiscordChannel]
    guild_channels_by_guild: dict[str, list[DiscordChannel]]
    threads_by_guild: dict[str, list[DiscordChannel]] = field(default_factory=dict)
    scope_filter: ScopeFilter = field(default_factory=ScopeFilter)

    @property
    def thread_mode(self) -> ThreadDiscoveryMode:
        return self.scope_filter.thread_discovery_mode

    @property
    def includes_threads(self) -> bool:
        return self.thread_mode != "none"

    @property
    def includes_archived_threads(self) -> bool:
        return self.thread_mode == "all"

    @classmethod
    def fetch(
        cls,
        api: DiscordAPI,
        *,
        scope_filter: ScopeFilter | None = None,
        seed: ScopeDiscoverySeed | None = None,
    ) -> "ScopeInventory":
        scope_filter = scope_filter or ScopeFilter()
        thread_mode = scope_filter.thread_discovery_mode
        guilds = list(seed.guilds) if seed is not None else api.get_guilds()
        root_channels = (
            list(seed.root_channels) if seed is not None else api.get_root_channels()
        )
        guild_channels_by_guild = {}
        threads_by_guild = {}
        for guild in guilds:
            guild_id = guild.get("id")
            if guild_id is None:
                continue
            guild_id = str(guild_id)
            if seed is not None and seed.guild_ids is not None and guild_id not in seed.guild_ids:
                continue
            try:
                guild_channels = api.get_guild_channels(guild_id)
                guild_channels_by_guild[guild_id] = guild_channels
            except ResourceUnavailable as exc:
                api.logger.warning(
                    "Skipping guild %s as it is unavailable. Error: %s",
                    sensitive(guild_id),
                    str(exc),
                )
                continue

            if thread_mode != "none":
                threads_by_guild[guild_id] = cls._fetch_guild_threads(
                    api=api,
                    guild_id=guild_id,
                    guild_channels=guild_channels,
                    scope_filter=scope_filter,
                )
        return cls(
            guilds=guilds,
            root_channels=root_channels,
            guild_channels_by_guild=guild_channels_by_guild,
            threads_by_guild=threads_by_guild,
            scope_filter=scope_filter,
        )

    @staticmethod
    def _fetch_guild_threads(
        api: DiscordAPI,
        guild_id: str,
        guild_channels: list[DiscordChannel],
        scope_filter: ScopeFilter,
    ) -> list[DiscordChannel]:
        parent_by_id = {
            str(channel["id"]): channel
            for channel in guild_channels
            if channel.get("id") is not None
        }
        threads_by_id: dict[str, DiscordChannel] = {}

        for parent in guild_channels:
            for thread in ScopeInventory.fetch_parent_threads(
                api=api,
                guild_id=guild_id,
                parent=parent,
                parent_by_id=parent_by_id,
                scope_filter=scope_filter,
            ):
                threads_by_id[str(thread["id"])] = thread

        return list(threads_by_id.values())

    @staticmethod
    def fetch_parent_threads(
        api: DiscordAPI,
        guild_id: str,
        parent: DiscordChannel,
        parent_by_id: dict[str, DiscordChannel],
        scope_filter: ScopeFilter,
    ) -> list[DiscordChannel]:
        """Fetch and normalize one thread parent's accessible included threads."""
        parent_id = parent.get("id")
        if parent_id is None or not scope_filter.searches_thread_parent(parent.get("type")):
            return []
        try:
            channel_threads = api.search_channel_threads(
                str(parent_id),
                include_archived=scope_filter.thread_discovery_mode == "all",
            )
        except ResourceUnavailable as exc:
            api.logger.diagnostic(
                "Skipping threads for channel %s as they are unavailable. Error: %s",
                sensitive(parent_id),
                str(exc),
            )
            return []

        threads_by_id: dict[str, DiscordChannel] = {}
        for thread in channel_threads:
            normalized = ScopeInventory._normalize_thread(thread, guild_id, parent_by_id)
            thread_id = normalized.get("id")
            if thread_id is not None and scope_filter.includes_channel(normalized):
                threads_by_id[str(thread_id)] = normalized
        return list(threads_by_id.values())

    @staticmethod
    def _normalize_thread(
        thread: DiscordChannel,
        guild_id: str,
        parent_by_id: dict[str, DiscordChannel],
    ) -> DiscordChannel:
        normalized = dict(thread)
        normalized.setdefault("guild_id", guild_id)
        parent_id = normalized.get("parent_id")
        parent = parent_by_id.get(str(parent_id)) if parent_id is not None else None
        if parent and parent.get("parent_id") is not None:
            normalized["category_id"] = str(parent["parent_id"])
        return normalized

    def guild_channels(self, guild_id: str) -> list[DiscordChannel]:
        return self.guild_channels_by_guild.get(str(guild_id), [])

    def guild_threads(self, guild_id: str) -> list[DiscordChannel]:
        return self.threads_by_guild.get(str(guild_id), [])

    def all_guild_channels(self) -> list[DiscordChannel]:
        channels = []
        for guild_id, guild_channels in self.guild_channels_by_guild.items():
            for channel in guild_channels:
                if "guild_id" not in channel:
                    channel = dict(channel)
                    channel["guild_id"] = guild_id
                channels.append(channel)
            channels.extend(self.guild_threads(guild_id))
        return channels

    def all_channels(self) -> list[DiscordChannel]:
        return [*self.root_channels, *self.all_guild_channels()]


def iter_cleanup_channels(
    api: DiscordAPI,
    *,
    scope_filter: ScopeFilter,
    inventory: ScopeInventory | None = None,
    seed: ScopeDiscoverySeed | None = None,
) -> Generator[DiscordChannel, None, None]:
    """Yield cleanup channels eagerly from an inventory or lazily from Discord."""
    if inventory is not None:
        yield from _iter_inventory_cleanup_channels(inventory)
        return
    yield from _iter_discovered_cleanup_channels(api, scope_filter, seed)


def _iter_discovered_cleanup_channels(
    api: DiscordAPI,
    scope_filter: ScopeFilter,
    seed: ScopeDiscoverySeed | None = None,
) -> Generator[DiscordChannel, None, None]:
    guilds = list(seed.guilds) if seed is not None else api.get_guilds()
    root_channels = (
        list(seed.root_channels) if seed is not None else api.get_root_channels()
    )
    yield from (
        channel
        for channel in root_channels
        if channel.get("type") in ROOT_MESSAGE_CHANNEL_TYPES
    )

    for guild in guilds:
        guild_id = guild.get("id")
        if guild_id is None:
            continue
        guild_id = str(guild_id)
        if seed is not None and seed.guild_ids is not None and guild_id not in seed.guild_ids:
            continue
        try:
            guild_channels = api.get_guild_channels(guild_id)
        except ResourceUnavailable as exc:
            _api_logger(api).warning(
                "Skipping guild %s as it is unavailable. Error: %s",
                sensitive(guild_id),
                str(exc),
            )
            continue

        parent_by_id = {
            str(channel["id"]): channel
            for channel in guild_channels
            if channel.get("id") is not None
        }
        yielded_thread_ids: set[str] = set()
        for raw_channel in guild_channels:
            channel = _with_guild_id(raw_channel, guild_id)
            parent_threads = ScopeInventory.fetch_parent_threads(
                api=api,
                guild_id=guild_id,
                parent=channel,
                parent_by_id=parent_by_id,
                scope_filter=scope_filter,
            )
            if channel.get("type") in GUILD_CLEANUP_CHANNEL_TYPES:
                yield channel

            for thread in parent_threads:
                thread_id = str(thread["id"])
                if thread_id in yielded_thread_ids:
                    continue
                yielded_thread_ids.add(thread_id)
                yield thread


def _iter_inventory_cleanup_channels(
    inventory: ScopeInventory,
) -> Generator[DiscordChannel, None, None]:
    yield from (
        channel
        for channel in inventory.root_channels
        if channel.get("type") in ROOT_MESSAGE_CHANNEL_TYPES
    )

    seen_guild_ids: set[str] = set()
    guild_ids: list[str] = []
    for guild in inventory.guilds:
        guild_id = guild.get("id")
        if guild_id is None or str(guild_id) in seen_guild_ids:
            continue
        guild_id = str(guild_id)
        seen_guild_ids.add(guild_id)
        guild_ids.append(guild_id)
    for guild_id in inventory.guild_channels_by_guild:
        if guild_id not in seen_guild_ids:
            guild_ids.append(guild_id)

    for guild_id in guild_ids:
        guild_channels = inventory.guild_channels(guild_id)
        threads = inventory.guild_threads(guild_id)
        threads_by_parent: dict[str, list[DiscordChannel]] = {}
        for thread in threads:
            parent_id = thread.get("parent_id")
            if parent_id is not None:
                threads_by_parent.setdefault(str(parent_id), []).append(thread)

        yielded_thread_ids: set[str] = set()
        for raw_channel in guild_channels:
            channel = _with_guild_id(raw_channel, guild_id)
            if channel.get("type") in GUILD_CLEANUP_CHANNEL_TYPES:
                yield channel
            channel_id = channel.get("id")
            if channel_id is None:
                continue
            for thread in threads_by_parent.get(str(channel_id), []):
                thread_id = str(thread["id"])
                if thread_id in yielded_thread_ids:
                    continue
                yielded_thread_ids.add(thread_id)
                yield thread

        yield from (
            thread
            for thread in threads
            if str(thread.get("id")) not in yielded_thread_ids
        )


def _api_logger(api: DiscordAPI):
    return getattr(api, "logger", logging.getLogger("scope_inventory"))


def _with_guild_id(channel: DiscordChannel, guild_id: str) -> DiscordChannel:
    if channel.get("guild_id") is not None:
        return channel
    normalized = dict(channel)
    normalized["guild_id"] = guild_id
    return normalized
