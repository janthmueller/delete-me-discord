from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace
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
    included_channel_types: frozenset[ChannelType] = field(default_factory=frozenset)
    excluded_channel_types: frozenset[ChannelType] = field(default_factory=frozenset)
    included_thread_states: frozenset[ThreadState] = field(default_factory=frozenset)
    excluded_thread_states: frozenset[ThreadState] = field(default_factory=frozenset)
    exact_included_channel_ids: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_names(
        cls,
        excluded_channel_types: Iterable[str] | None = None,
        excluded_thread_states: Iterable[str] | None = None,
        exclude_threads: bool = False,
        *,
        included_channel_types: Iterable[str] | None = None,
        included_thread_states: Iterable[str] | None = None,
        include_threads: bool = False,
        exact_included_channel_ids: Iterable[str] | None = None,
    ) -> "ScopeFilter":
        explicitly_included_type_names = list(included_channel_types or [])
        included_type_names = list(explicitly_included_type_names)
        if include_threads:
            included_type_names.extend(THREAD_CHANNEL_TYPE_NAMES)
        type_names = list(excluded_channel_types or [])
        if exclude_threads:
            type_names.extend(
                name
                for name in THREAD_CHANNEL_TYPE_NAMES
                if name not in explicitly_included_type_names
            )
        included_state_names = list(included_thread_states or [])
        state_names = list(excluded_thread_states or [])
        unknown_types = sorted(
            (set(included_type_names) | set(type_names))
            - FILTERABLE_CHANNEL_TYPES_BY_NAME.keys()
        )
        if unknown_types:
            raise ValueError(
                "Unknown excluded channel type(s): " + ", ".join(unknown_types) + "."
            )
        unknown_states = sorted(
            (set(included_state_names) | set(state_names)) - set(THREAD_STATES)
        )
        if unknown_states:
            raise ValueError(
                "Unknown excluded thread state(s): " + ", ".join(unknown_states) + "."
            )
        return cls(
            included_channel_types=frozenset(
                FILTERABLE_CHANNEL_TYPES_BY_NAME[name] for name in included_type_names
            ),
            excluded_channel_types=frozenset(
                FILTERABLE_CHANNEL_TYPES_BY_NAME[name] for name in type_names
            ),
            included_thread_states=frozenset(included_state_names),
            excluded_thread_states=frozenset(state_names),
            exact_included_channel_ids=frozenset(
                str(value) for value in exact_included_channel_ids or ()
            ),
        )

    @classmethod
    def without_threads(cls) -> "ScopeFilter":
        return cls(excluded_channel_types=frozenset(THREAD_CHANNEL_TYPES))

    def with_exact_included_channel_ids(
        self,
        channel_ids: Iterable[str],
    ) -> "ScopeFilter":
        return replace(
            self,
            exact_included_channel_ids=frozenset(str(value) for value in channel_ids),
        )

    @property
    def thread_discovery_mode(self) -> ThreadDiscoveryMode:
        included_thread_types = self._broadly_included_thread_types
        if not included_thread_types or set(THREAD_STATES) <= self.excluded_thread_states:
            return "none"
        included_states = self.included_thread_states or frozenset(THREAD_STATES)
        included_states -= self.excluded_thread_states
        if not included_states:
            return "none"
        if included_states == {"active"}:
            return "active"
        if "archived" in self.excluded_thread_states:
            return "active"
        return "all"

    def includes_channel(self, channel: Mapping[str, Any]) -> bool:
        channel_id = channel.get("id")
        if (
            channel_id is not None
            and str(channel_id) in self.exact_included_channel_ids
        ):
            return True
        channel_type = channel.get("type")
        if channel_type in self.excluded_channel_types:
            return False
        if channel_type in THREAD_CHANNEL_TYPES:
            state: ThreadState = "archived" if is_archived_thread(channel) else "active"
            if state in self.excluded_thread_states:
                return False
            included_thread_types = self._broadly_included_thread_types
            if channel_type not in included_thread_types:
                return False
            if self.included_thread_states and state not in self.included_thread_states:
                return False
            return True
        if self.included_channel_types:
            return channel_type in self.included_channel_types
        if self.included_thread_states:
            return False
        return True

    def searches_thread_parent(self, parent_type: Any) -> bool:
        if self.thread_discovery_mode == "none":
            return False
        possible_types = THREAD_TYPES_BY_PARENT.get(parent_type, frozenset())
        return bool(possible_types & self._broadly_included_thread_types)

    @property
    def _broadly_included_thread_types(self) -> frozenset[ChannelType]:
        selected_thread_types = self.included_channel_types & THREAD_CHANNEL_TYPES
        selected_non_thread_types = self.included_channel_types - THREAD_CHANNEL_TYPES
        if selected_thread_types:
            included = selected_thread_types
        elif self.included_thread_states or not selected_non_thread_types:
            included = THREAD_CHANNEL_TYPES
        else:
            included = frozenset()
        return included - self.excluded_channel_types
