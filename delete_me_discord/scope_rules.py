from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .privacy import sensitive


@dataclass(frozen=True, slots=True)
class ScopeRules:
    """Pure nearest-target include/exclude policy for one channel ancestry path."""

    include_ids: frozenset[str]
    exclude_ids: frozenset[str]

    @classmethod
    def from_values(
        cls,
        include_ids: Iterable[str] | None = None,
        exclude_ids: Iterable[str] | None = None,
    ) -> "ScopeRules":
        rules = cls(
            include_ids=frozenset(str(value) for value in include_ids or []),
            exclude_ids=frozenset(str(value) for value in exclude_ids or []),
        )
        overlap = sorted(rules.include_ids & rules.exclude_ids)
        if overlap:
            rendered = ", ".join(str(sensitive(value)) for value in overlap)
            raise ValueError(f"Include and exclude IDs must be disjoint: {rendered}.")
        return rules

    @property
    def has_includes(self) -> bool:
        return bool(self.include_ids)

    def includes(self, channel: Mapping[str, Any]) -> bool:
        """Apply the nearest explicit rule, defaulting to false when includes exist."""
        scope_chain = (
            channel.get("id"),
            channel.get("parent_id"),
            channel.get("category_id"),
            channel.get("guild_id"),
        )
        for scope_id in scope_chain:
            if scope_id is None:
                continue
            scope_id = str(scope_id)
            if scope_id in self.exclude_ids:
                return False
            if scope_id in self.include_ids:
                return True
        return not self.has_includes
