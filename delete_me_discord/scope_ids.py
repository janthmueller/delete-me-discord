from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from .api import DiscordAPI
from .channel_types import (
    GUILD_MESSAGE_CHANNEL_TYPES,
    ROOT_MESSAGE_CHANNEL_TYPES,
    THREAD_CHANNEL_TYPES,
    THREAD_CONTAINER_CHANNEL_TYPES,
    ChannelType,
)
from .models import DiscordChannel
from .privacy import sensitive
from .scope_inventory import ScopeDiscoverySeed
from .scope_rules import ScopeRules
from .utils import ResourceUnavailable


ScopeNodeKind = Literal[
    "guild",
    "private-channel",
    "category",
    "message-channel",
    "thread-parent",
    "thread",
]


@dataclass(frozen=True, slots=True)
class ScopeNode:
    id: str
    kind: ScopeNodeKind
    guild_id: str | None = None
    parent_id: str | None = None
    channel_type: int | None = None


@dataclass(frozen=True)
class ScopePreflight:
    include_ids: tuple[str, ...]
    exclude_ids: tuple[str, ...]
    rules: ScopeRules
    nodes_by_id: dict[str, ScopeNode]
    seed: ScopeDiscoverySeed


def preflight_scope_ids(
    api: DiscordAPI,
    include_ids: Iterable[str] | None,
    exclude_ids: Iterable[str] | None,
) -> ScopePreflight:
    """Validate exact scope IDs and return reusable data for lazy traversal."""
    normalized_include = _normalize_ids(include_ids)
    normalized_exclude = _normalize_ids(exclude_ids)
    rules = ScopeRules.from_values(normalized_include, normalized_exclude)

    guilds = api.get_guilds()
    root_channels = api.get_root_channels()
    guild_by_id = _objects_by_id(guilds)
    root_by_id = _objects_by_id(root_channels)
    nodes_by_id: dict[str, ScopeNode] = {}
    resolved_channels_by_id: dict[str, DiscordChannel] = {}

    for scope_id in (*normalized_include, *normalized_exclude):
        if scope_id in nodes_by_id:
            continue
        if scope_id in guild_by_id:
            nodes_by_id[scope_id] = ScopeNode(id=scope_id, kind="guild")
            continue
        if scope_id in root_by_id:
            channel = root_by_id[scope_id]
            _require_supported_root_channel(scope_id, channel)
            nodes_by_id[scope_id] = _node_from_channel(scope_id, channel)
            resolved_channels_by_id[scope_id] = channel
            continue

        try:
            channel = api.get_channel(scope_id)
        except ResourceUnavailable as exc:
            raise ValueError(
                f"Discord ID '{sensitive(scope_id)}' could not be validated as an accessible "
                "supported scope target. Exact Discord IDs are required."
            ) from exc
        node = _node_from_channel(scope_id, channel)
        if node.kind == "private-channel":
            root_channels.append(channel)
            root_by_id[scope_id] = channel
        elif node.guild_id not in guild_by_id:
            raise ValueError(
                f"Discord ID '{sensitive(scope_id)}' belongs to a guild that is not accessible "
                "to the authenticated user."
            )
        nodes_by_id[scope_id] = node
        resolved_channels_by_id[scope_id] = channel

    included_guild_ids = frozenset(
        node.id if node.kind == "guild" else node.guild_id
        for scope_id in normalized_include
        for node in [nodes_by_id[scope_id]]
        if node.kind == "guild" or node.guild_id is not None
    )
    guild_ids = included_guild_ids if rules.has_includes else None
    return ScopePreflight(
        include_ids=normalized_include,
        exclude_ids=normalized_exclude,
        rules=rules,
        nodes_by_id=nodes_by_id,
        seed=ScopeDiscoverySeed(
            guilds=tuple(guilds),
            root_channels=tuple(root_channels),
            guild_ids=guild_ids,
            rules=rules,
            resolved_channels_by_id=resolved_channels_by_id,
            exact_included_thread_ids=frozenset(
                scope_id
                for scope_id in normalized_include
                if nodes_by_id[scope_id].kind == "thread"
            ),
        ),
    )


def _normalize_ids(values: Iterable[str] | None) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values or []:
        value = str(raw_value)
        if not value.isascii() or not value.isdigit():
            raise ValueError(
                f"Discord ID '{sensitive(value)}' is invalid. Discord IDs must contain only ASCII decimal digits."
            )
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    return tuple(normalized)


def _objects_by_id(objects) -> dict[str, dict]:
    return {
        str(item["id"]): item
        for item in objects
        if item.get("id") is not None
    }


def _require_supported_root_channel(scope_id: str, channel: DiscordChannel) -> None:
    if channel.get("type") not in ROOT_MESSAGE_CHANNEL_TYPES:
        raise ValueError(
            f"Discord ID '{sensitive(scope_id)}' has an unsupported private channel type."
        )


def _node_from_channel(scope_id: str, channel: DiscordChannel) -> ScopeNode:
    returned_id = channel.get("id")
    if returned_id is None or str(returned_id) != scope_id:
        raise ValueError(
            f"Discord returned mismatched data while validating ID '{sensitive(scope_id)}'."
        )

    channel_type = channel.get("type")
    if channel_type in ROOT_MESSAGE_CHANNEL_TYPES:
        kind: ScopeNodeKind = "private-channel"
    elif channel_type == ChannelType.GUILD_CATEGORY:
        kind = "category"
    elif channel_type in GUILD_MESSAGE_CHANNEL_TYPES:
        kind = "message-channel"
    elif channel_type in THREAD_CONTAINER_CHANNEL_TYPES:
        kind = "thread-parent"
    elif channel_type in THREAD_CHANNEL_TYPES:
        kind = "thread"
    else:
        raise ValueError(
            f"Discord ID '{sensitive(scope_id)}' has unsupported channel type {channel_type}."
        )

    guild_id = channel.get("guild_id")
    parent_id = channel.get("parent_id")
    if kind != "private-channel" and guild_id is None:
        raise ValueError(
            f"Discord omitted guild_id while validating ID '{sensitive(scope_id)}'."
        )
    return ScopeNode(
        id=scope_id,
        kind=kind,
        guild_id=str(guild_id) if guild_id is not None else None,
        parent_id=str(parent_id) if parent_id is not None else None,
        channel_type=channel_type,
    )
