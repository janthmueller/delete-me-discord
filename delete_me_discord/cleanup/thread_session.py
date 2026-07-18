"""Mutable archived-thread recovery state for one cleanup transaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from ..discord.models import DiscordChannel
from .thread_recovery import ActiveThreadBaseline, ArchivedThreadActivation

if TYPE_CHECKING:
    from .threads import ArchivedThreadCoordinator, ThreadRestoreOutcome


@dataclass(slots=True)
class ThreadRecoverySession:
    """Own mutable recovery state for one channel cleanup transaction."""

    coordinator: ArchivedThreadCoordinator
    channel: DiscordChannel
    guild: Mapping[str, Any] | None
    parent: DiscordChannel | None
    mode: str
    baseline: ActiveThreadBaseline | None = None
    activation: ArchivedThreadActivation | None = None
    reopen_count: int = 0
    _stopped: bool = False
    _restored: bool = False

    @classmethod
    def from_active_thread(
        cls,
        coordinator: ArchivedThreadCoordinator,
        channel: DiscordChannel,
        *,
        guild: Mapping[str, Any] | None,
        parent: DiscordChannel | None,
        mode: str,
    ) -> ThreadRecoverySession:
        return cls(
            coordinator=coordinator,
            channel=channel,
            guild=guild,
            parent=parent,
            mode=mode,
            baseline=coordinator.observe_active(channel),
        )

    @classmethod
    def from_activation(
        cls,
        coordinator: ArchivedThreadCoordinator,
        channel: DiscordChannel,
        activation: ArchivedThreadActivation,
        *,
        guild: Mapping[str, Any] | None,
        parent: DiscordChannel | None,
        mode: str,
    ) -> ThreadRecoverySession:
        return cls(
            coordinator=coordinator,
            channel=channel,
            guild=guild,
            parent=parent,
            mode=mode,
            activation=activation,
        )

    def resume(self) -> bool:
        """Handle one archived outcome and update the current activation state."""
        if self._stopped or self._restored:
            return False

        if self.activation is not None:
            result = self.coordinator.resume_after_likely_auto_archive(
                self.channel,
                self.activation,
            )
            self.activation = result.activation
            retry_action = result.retry_action
        elif self.baseline is not None:
            result = self.coordinator.resume_active_after_likely_auto_archive(
                self.channel,
                self.baseline,
                guild=self.guild,
                parent=self.parent,
                mode=self.mode,
            )
            self.activation = result.activation
            retry_action = result.retry_action
        else:
            retry_action = False

        if retry_action:
            self.reopen_count += 1
        else:
            self._stopped = True
        return retry_action

    def restore(self) -> ThreadRestoreOutcome | None:
        """Restore the latest active transition at most once."""
        if self._restored:
            return None
        self._restored = True
        activation = self.activation
        if activation is None or not activation.opened:
            return None
        return self.coordinator.restore(self.channel, activation)
