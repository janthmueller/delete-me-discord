from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .channel_types import (
    ChannelType,
    GUILD_MESSAGE_CHANNEL_TYPES,
    ROOT_MESSAGE_CHANNEL_TYPES,
    THREAD_CHANNEL_TYPES,
    THREAD_CONTAINER_CHANNEL_TYPES,
    channel_type_name,
)
from .privacy import sensitive, sensitive_name
from .scope_inventory import ScopeInventory


@dataclass(frozen=True)
class ScopeTarget:
    id: str
    kind: str
    name: str

    def render(self) -> str:
        return f"{self.kind} {sensitive_name(self.name)} (ID: {sensitive(self.id)})"


def discover_scope_targets(inventory: ScopeInventory) -> list[ScopeTarget]:
    """Discover all target IDs accepted by include_ids/exclude_ids filters."""
    targets: list[ScopeTarget] = []

    for guild in inventory.guilds:
        guild_id = _string_id(guild.get("id"))
        if guild_id:
            targets.append(ScopeTarget(id=guild_id, kind="Guild", name=guild.get("name") or "Unknown"))

    for channel in inventory.root_channels:
        channel_type = channel.get("type")
        kind = channel_type_name(channel_type) if channel_type in ROOT_MESSAGE_CHANNEL_TYPES else None
        channel_id = _string_id(channel.get("id"))
        if kind and channel_id:
            targets.append(ScopeTarget(id=channel_id, kind=kind, name=_channel_name(channel)))

    for guild in inventory.guilds:
        guild_id = _string_id(guild.get("id"))
        if not guild_id:
            continue
        channels = inventory.guild_channels(guild_id)
        threads = inventory.guild_threads(guild_id)
        eligible_category_ids = {
            _string_id(channel.get("parent_id"))
            for channel in channels
            if (
                channel.get("type") in GUILD_MESSAGE_CHANNEL_TYPES
                or (inventory.includes_threads and channel.get("type") in THREAD_CONTAINER_CHANNEL_TYPES)
            )
            and channel.get("parent_id") is not None
        }
        eligible_category_ids.update(
            _string_id(thread.get("category_id"))
            for thread in threads
            if thread.get("category_id") is not None
        )
        for channel in channels:
            channel_type = channel.get("type")
            channel_id = _string_id(channel.get("id"))
            if channel_type == ChannelType.GUILD_CATEGORY:
                if channel_id in eligible_category_ids:
                    targets.append(ScopeTarget(id=channel_id, kind="Category", name=_channel_name(channel)))
                continue
            is_direct_message_channel = channel_type in GUILD_MESSAGE_CHANNEL_TYPES
            is_thread_container = inventory.includes_threads and channel_type in THREAD_CONTAINER_CHANNEL_TYPES
            if channel_id and (is_direct_message_channel or is_thread_container):
                targets.append(
                    ScopeTarget(id=channel_id, kind=channel_type_name(channel_type), name=_channel_name(channel))
                )

        if inventory.includes_threads:
            for thread in threads:
                thread_type = thread.get("type")
                thread_id = _string_id(thread.get("id"))
                if thread_id and thread_type in THREAD_CHANNEL_TYPES:
                    targets.append(
                        ScopeTarget(id=thread_id, kind=channel_type_name(thread_type), name=_channel_name(thread))
                    )

    return targets


def resolve_scope_selectors(
    inventory: ScopeInventory,
    include_ids: Iterable[str] | None,
    exclude_ids: Iterable[str] | None,
) -> tuple[list[str], list[str]]:
    if not include_ids and not exclude_ids:
        return list(include_ids or []), list(exclude_ids or [])

    targets = discover_scope_targets(inventory)
    resolver = ScopeSelectorResolver(targets)
    resolved_include = resolver.resolve_all(include_ids or [])
    resolved_exclude = resolver.resolve_all(exclude_ids or [])
    overlap = sorted(set(resolved_include) & set(resolved_exclude))
    if overlap:
        rendered = ", ".join(str(sensitive(item)) for item in overlap)
        raise ValueError(f"Include and exclude IDs must be disjoint after selector resolution: {rendered}.")
    return resolved_include, resolved_exclude


class ScopeSelectorResolver:
    def __init__(self, targets: Iterable[ScopeTarget]):
        self._targets_by_id: dict[str, ScopeTarget] = {}
        for target in targets:
            self._targets_by_id.setdefault(target.id, target)

    def resolve_all(self, selectors: Iterable[str]) -> list[str]:
        return [self.resolve(selector) for selector in selectors]

    def resolve(self, selector: str) -> str:
        selector = str(selector)
        if selector in self._targets_by_id:
            return selector
        if not selector.isdigit():
            raise ValueError(
                f"ID selector '{selector}' did not match any discovered guild, category, channel, or DM."
            )

        matches = [
            target
            for target in self._targets_by_id.values()
            if target.id.endswith(selector)
        ]
        if len(matches) == 1:
            return matches[0].id
        if not matches:
            raise ValueError(
                f"ID selector '{selector}' did not match any discovered guild, category, channel, or DM."
            )

        rendered_matches = "\n".join(
            f"- {target.render()}"
            for target in sorted(matches, key=lambda item: (item.kind, item.name, item.id))
        )
        raise ValueError(
            f"Could not resolve ID selector '{selector}' uniquely. It matched multiple targets, so no action was taken. Use more digits.\n{rendered_matches}"
        )


def _string_id(value) -> str | None:
    if value is None:
        return None
    return str(value)


def _channel_name(channel) -> str:
    if channel.get("name"):
        return str(channel["name"])
    recipients = channel.get("recipients") or []
    names = [recipient.get("username", "Unknown") for recipient in recipients]
    return ", ".join(names) if names else "Unknown"
