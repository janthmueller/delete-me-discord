from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping

from .channel_types import (
    FILTERABLE_CHANNEL_TYPES_BY_NAME,
    THREAD_CHANNEL_TYPES,
    THREAD_CHANNEL_TYPE_NAMES,
    ChannelType,
    is_archived_thread,
)


ThreadDiscoveryMode = Literal["none", "active", "all"]
ThreadState = Literal["active", "archived"]
THREAD_STATES: tuple[ThreadState, ...] = ("active", "archived")

THREAD_TYPES_BY_PARENT: dict[ChannelType, frozenset[ChannelType]] = {
    ChannelType.GUILD_TEXT: frozenset({
        ChannelType.PUBLIC_THREAD,
        ChannelType.PRIVATE_THREAD,
    }),
    ChannelType.GUILD_ANNOUNCEMENT: frozenset({
        ChannelType.ANNOUNCEMENT_THREAD,
    }),
    ChannelType.GUILD_FORUM: frozenset({
        ChannelType.PUBLIC_THREAD,
    }),
    ChannelType.GUILD_MEDIA: frozenset({
        ChannelType.PUBLIC_THREAD,
    }),
}


@dataclass(frozen=True)
class ScopeFilter:
    excluded_channel_types: frozenset[ChannelType] = field(default_factory=frozenset)
    excluded_thread_states: frozenset[ThreadState] = field(default_factory=frozenset)

    @classmethod
    def from_names(
        cls,
        excluded_channel_types: Iterable[str] | None = None,
        excluded_thread_states: Iterable[str] | None = None,
        exclude_threads: bool = False,
    ) -> "ScopeFilter":
        type_names = list(excluded_channel_types or [])
        if exclude_threads:
            type_names.extend(THREAD_CHANNEL_TYPE_NAMES)
        state_names = list(excluded_thread_states or [])
        unknown_types = sorted(set(type_names) - FILTERABLE_CHANNEL_TYPES_BY_NAME.keys())
        if unknown_types:
            raise ValueError(
                "Unknown excluded channel type(s): " + ", ".join(unknown_types) + "."
            )
        unknown_states = sorted(set(state_names) - set(THREAD_STATES))
        if unknown_states:
            raise ValueError(
                "Unknown excluded thread state(s): " + ", ".join(unknown_states) + "."
            )
        return cls(
            excluded_channel_types=frozenset(
                FILTERABLE_CHANNEL_TYPES_BY_NAME[name] for name in type_names
            ),
            excluded_thread_states=frozenset(state_names),
        )

    @classmethod
    def without_threads(cls) -> "ScopeFilter":
        return cls(excluded_channel_types=frozenset(THREAD_CHANNEL_TYPES))

    @property
    def thread_discovery_mode(self) -> ThreadDiscoveryMode:
        included_thread_types = THREAD_CHANNEL_TYPES - self.excluded_channel_types
        if not included_thread_types or set(THREAD_STATES) <= self.excluded_thread_states:
            return "none"
        if "archived" in self.excluded_thread_states:
            return "active"
        return "all"

    def includes_channel(self, channel: Mapping[str, Any]) -> bool:
        channel_type = channel.get("type")
        if channel_type in self.excluded_channel_types:
            return False
        if channel_type in THREAD_CHANNEL_TYPES:
            state: ThreadState = "archived" if is_archived_thread(channel) else "active"
            if state in self.excluded_thread_states:
                return False
        return True

    def searches_thread_parent(self, parent_type: Any) -> bool:
        if self.thread_discovery_mode == "none":
            return False
        possible_types = THREAD_TYPES_BY_PARENT.get(parent_type, frozenset())
        return bool(possible_types - self.excluded_channel_types)
