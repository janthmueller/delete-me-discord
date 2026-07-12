from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .api import DiscordAPI
from .models import DiscordChannel
from .privacy import sensitive
from .scope_filter import ScopeFilter, ThreadDiscoveryMode
from .utils import ResourceUnavailable


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
    ) -> "ScopeInventory":
        scope_filter = scope_filter or ScopeFilter()
        thread_mode = scope_filter.thread_discovery_mode
        guilds = api.get_guilds()
        root_channels = api.get_root_channels()
        guild_channels_by_guild = {}
        threads_by_guild = {}
        for guild in guilds:
            guild_id = guild.get("id")
            if guild_id is None:
                continue
            guild_id = str(guild_id)
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
            parent_id = parent.get("id")
            if parent_id is None or not scope_filter.searches_thread_parent(parent.get("type")):
                continue
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
                continue
            for thread in channel_threads:
                normalized = ScopeInventory._normalize_thread(thread, guild_id, parent_by_id)
                if normalized.get("id") is not None and scope_filter.includes_channel(normalized):
                    threads_by_id[str(normalized["id"])] = normalized

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
