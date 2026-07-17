from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .channel_types import FILTERABLE_CHANNEL_TYPE_NAMES
from .scope_filter import THREAD_STATES


THREAD_GROUP_SELECTOR = "threads"
_CHANNEL_TYPE_NAMES = frozenset(FILTERABLE_CHANNEL_TYPE_NAMES)
_THREAD_STATES = frozenset(THREAD_STATES)


@dataclass(frozen=True, slots=True)
class ScopeSelectors:
    """Classified include/exclude selectors from the compact CLI syntax."""

    include_ids: tuple[str, ...] = ()
    exclude_ids: tuple[str, ...] = ()
    included_channel_types: tuple[str, ...] = ()
    excluded_channel_types: tuple[str, ...] = ()
    included_thread_states: tuple[str, ...] = ()
    excluded_thread_states: tuple[str, ...] = ()
    include_threads: bool = False
    exclude_threads: bool = False

def parse_scope_selectors(
    include_values: Iterable[str] | None,
    exclude_values: Iterable[str] | None,
) -> ScopeSelectors:
    include = _classify(include_values, option="--include")
    exclude = _classify(exclude_values, option="--exclude")
    return ScopeSelectors(
        include_ids=include.ids,
        exclude_ids=exclude.ids,
        included_channel_types=include.channel_types,
        excluded_channel_types=exclude.channel_types,
        included_thread_states=include.thread_states,
        excluded_thread_states=exclude.thread_states,
        include_threads=include.threads,
        exclude_threads=exclude.threads,
    )


@dataclass(frozen=True, slots=True)
class _ClassifiedSelectors:
    ids: tuple[str, ...]
    channel_types: tuple[str, ...]
    thread_states: tuple[str, ...]
    threads: bool


def _classify(
    values: Iterable[str] | None,
    *,
    option: str,
) -> _ClassifiedSelectors:
    ids: list[str] = []
    channel_types: list[str] = []
    thread_states: list[str] = []
    threads = False
    seen: set[str] = set()

    for raw_value in values or ():
        value = str(raw_value)
        if value in seen:
            continue
        seen.add(value)
        if value.isascii() and value.isdigit():
            ids.append(value)
        elif value in _CHANNEL_TYPE_NAMES:
            channel_types.append(value)
        elif value in _THREAD_STATES:
            thread_states.append(value)
        elif value == THREAD_GROUP_SELECTOR:
            threads = True
        else:
            expected = (
                "a complete decimal Discord ID, a canonical channel type, "
                "'threads', 'active', or 'archived'"
            )
            raise ValueError(f"Unknown {option} selector '{value}'; expected {expected}.")

    return _ClassifiedSelectors(
        ids=tuple(ids),
        channel_types=tuple(channel_types),
        thread_states=tuple(thread_states),
        threads=threads,
    )
