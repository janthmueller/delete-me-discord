from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .api import DiscordAPI
from .models import DiscordChannel
from .privacy import sensitive
from .utils import ResourceUnavailable


@dataclass(frozen=True)
class ScopeInventory:
    guilds: list[dict[str, Any]]
    root_channels: list[DiscordChannel]
    guild_channels_by_guild: dict[str, list[DiscordChannel]]

    @classmethod
    def fetch(cls, api: DiscordAPI) -> "ScopeInventory":
        guilds = api.get_guilds()
        root_channels = api.get_root_channels()
        guild_channels_by_guild = {}
        for guild in guilds:
            guild_id = guild.get("id")
            if guild_id is None:
                continue
            guild_id = str(guild_id)
            try:
                guild_channels_by_guild[guild_id] = api.get_guild_channels(guild_id)
            except ResourceUnavailable as exc:
                api.logger.warning(
                    "Skipping guild %s as it is unavailable. Error: %s",
                    sensitive(guild_id),
                    str(exc),
                )
        return cls(
            guilds=guilds,
            root_channels=root_channels,
            guild_channels_by_guild=guild_channels_by_guild,
        )

    def guild_channels(self, guild_id: str) -> list[DiscordChannel]:
        return self.guild_channels_by_guild.get(str(guild_id), [])

    def all_guild_channels(self) -> list[DiscordChannel]:
        channels = []
        for guild_id, guild_channels in self.guild_channels_by_guild.items():
            for channel in guild_channels:
                if "guild_id" not in channel:
                    channel = dict(channel)
                    channel["guild_id"] = guild_id
                channels.append(channel)
        return channels

    def all_channels(self) -> list[DiscordChannel]:
        return [*self.root_channels, *self.all_guild_channels()]
