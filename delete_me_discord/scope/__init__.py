"""Scope selector parsing, filtering, resolution, and traversal."""

from .filter import (
    THREAD_STATES,
    ScopeFilter,
    ThreadDiscoveryMode,
    ThreadState,
)
from .inventory import (
    CleanupChannelContext,
    ScopeDiscoverySeed,
    ScopeInventory,
    iter_cleanup_channel_contexts,
)
from .resolver import ScopeNode, ScopeNodeKind, ScopePreflight, preflight_scope_ids
from .rules import ScopeRules, should_include_channel
from .selectors import ScopeSelectors, parse_scope_selectors

__all__ = [
    "CleanupChannelContext",
    "ScopeDiscoverySeed",
    "ScopeFilter",
    "ScopeInventory",
    "ScopeNode",
    "ScopeNodeKind",
    "ScopePreflight",
    "ScopeRules",
    "ScopeSelectors",
    "THREAD_STATES",
    "ThreadDiscoveryMode",
    "ThreadState",
    "iter_cleanup_channel_contexts",
    "parse_scope_selectors",
    "preflight_scope_ids",
    "should_include_channel",
]
