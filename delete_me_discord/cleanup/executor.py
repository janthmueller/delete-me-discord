import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from ..discord.models import DeleteOutcome, DiscordEmoji, DiscordMessage
from ..logging import format_timestamp, structured_event
from ..privacy import sensitive
from ..discord.rate_limits import DELETE_POLICY
from ..discord.type_enums import ReactionType
from .models import (
    ActionKind,
    ChannelCleanupStats,
    ChannelExecutionResult,
    ChannelPlan,
    MessageDecision,
    MessageFacts,
    PlannedAction,
)
from .planner import CleanupPlanner


@dataclass(slots=True)
class _DeleteActionCounts:
    deleted: int = 0
    absent: int = 0
    failed: int = 0
    processed: int = 0
    thread_archived: bool = False

    def record(self, outcome: DeleteOutcome) -> None:
        self.processed += 1
        if outcome == DeleteOutcome.DELETED:
            self.deleted += 1
        elif outcome == DeleteOutcome.ABSENT:
            self.absent += 1
        else:
            self.failed += 1
            if outcome == DeleteOutcome.THREAD_ARCHIVED:
                self.thread_archived = True


class ChannelExecutor:
    """Execute or simulate one channel plan and collect outcome statistics."""

    def __init__(
        self,
        api: Any,
        logger: Any,
        configure_request_policy: Callable[[str, tuple[float, float]], None],
    ):
        self.api = api
        self.logger = logger
        self.configure_request_policy = configure_request_policy

    def execute(
        self,
        messages: Iterable[DiscordMessage],
        planner: CleanupPlanner,
        delete_sleep_time_range: tuple[float, float],
        *,
        dry_run: bool = False,
        channel_plan: Optional[ChannelPlan] = None,
        resume_archived_thread: Optional[Callable[[], bool]] = None,
    ) -> ChannelExecutionResult:
        preserved_msg_ids = (
            [
                decision.facts.message["message_id"]
                for decision in channel_plan.decisions
                if (
                    decision.preserve_message
                    and decision.facts.is_deletable
                )
                or decision.preserve_reaction_count > 0
            ]
            if channel_plan is not None
            else []
        )
        self.configure_request_policy(DELETE_POLICY, delete_sleep_time_range)
        stats = ChannelCleanupStats()
        decisions: Iterable[MessageDecision]
        if channel_plan is not None:
            decisions = channel_plan.decisions
            stats.message_count = channel_plan.buffered_message_count
            stats.preserved_deletable_count = sum(
                decision.preserve_message and decision.facts.is_deletable
                for decision in channel_plan.decisions
            )
            stats.preserved_reactions_count = sum(
                decision.preserve_reaction_count
                for decision in channel_plan.decisions
            )
        else:
            decisions = planner.iter_message_decisions(messages)

        action_start = time.monotonic()
        processed_action_count = 0
        interrupted_channel_id: str | None = None
        for decision in decisions:
            if channel_plan is None:
                stats.message_count += 1
            facts = decision.facts
            message_id = facts.message["message_id"]

            if decision.preserve_message and facts.is_deletable:
                self.logger.detail(
                    "Preserving deletable message %s sent at %s UTC.",
                    sensitive(message_id),
                    format_timestamp(facts.message_time),
                    extra=structured_event(
                        "cleanup.decision",
                        mode="dry-run" if dry_run else "execute",
                        artifact="message",
                        decision="keep",
                        count=1,
                    ),
                )
                if channel_plan is None:
                    stats.preserved_deletable_count += 1
                    preserved_msg_ids.append(message_id)
                continue

            if decision.preserve_reaction_count > 0:
                reaction_count = decision.preserve_reaction_count
                self.logger.detail(
                    "Preserving %s reaction%s on message %s.",
                    reaction_count,
                    "" if reaction_count == 1 else "s",
                    sensitive(message_id),
                    extra=structured_event(
                        "cleanup.decision",
                        mode="dry-run" if dry_run else "execute",
                        artifact="reaction",
                        decision="keep",
                        count=reaction_count,
                    ),
                )
                if channel_plan is None:
                    stats.preserved_reactions_count += (
                        reaction_count
                    )
                    preserved_msg_ids.append(message_id)
                continue

            reaction_actions: list[PlannedAction] = []
            for action in decision.actions:
                if action.kind == ActionKind.DELETE_REACTION:
                    reaction_actions.append(action)
                    continue
                executed = self.execute_action(
                    action=action,
                    dry_run=dry_run,
                    facts=facts,
                )
                if (
                    executed == DeleteOutcome.THREAD_ARCHIVED
                    and resume_archived_thread is not None
                    and resume_archived_thread()
                ):
                    executed = self.execute_action(
                        action=action,
                        dry_run=dry_run,
                        facts=facts,
                    )
                processed_action_count += 1
                if dry_run or executed == DeleteOutcome.DELETED:
                    stats.deleted_count += 1
                    stats.add_foreign_reaction_impact(
                        facts.foreign_reaction_impact
                    )
                elif executed == DeleteOutcome.ABSENT:
                    stats.absent_count += 1
                else:
                    stats.failed_count += 1
                if executed == DeleteOutcome.THREAD_ARCHIVED:
                    interrupted_channel_id = action.channel_id
                    break

            if interrupted_channel_id is not None:
                break
            if reaction_actions:
                reaction_outcomes = self._execute_reaction_actions(
                    actions=reaction_actions,
                    dry_run=dry_run,
                    resume_archived_thread=resume_archived_thread,
                )
                processed_action_count += reaction_outcomes.processed
                stats.reactions_removed_count += reaction_outcomes.deleted
                stats.reactions_absent_count += reaction_outcomes.absent
                stats.reactions_failed_count += reaction_outcomes.failed
                if reaction_outcomes.thread_archived:
                    interrupted_channel_id = reaction_actions[0].channel_id
                    break

        if interrupted_channel_id is not None:
            stats.thread_state_interrupted_count = 1
            if channel_plan is not None:
                stats.actions_not_attempted_count = max(
                    0,
                    channel_plan.action_count - processed_action_count,
                )
                self.logger.warning(
                    "Thread %s archived during cleanup; %s remaining planned action(s) were not attempted.",
                    sensitive(interrupted_channel_id),
                    stats.actions_not_attempted_count,
                )
            else:
                self.logger.warning(
                    "Thread %s archived during cleanup; remaining actions were not attempted.",
                    sensitive(interrupted_channel_id),
                )

        return ChannelExecutionResult(
            preserved_message_ids=tuple(preserved_msg_ids),
            stats=stats,
            action_elapsed=time.monotonic() - action_start,
        )

    def execute_action(
        self,
        action: PlannedAction,
        dry_run: bool,
        facts: Optional[MessageFacts] = None,
    ) -> Optional[DeleteOutcome]:
        """Execute a single planned action or simulate it in dry-run mode."""
        if action.kind == ActionKind.DELETE_MESSAGE:
            event = structured_event(
                "cleanup.action",
                mode="dry-run" if dry_run else "execute",
                artifact="message",
                action="delete",
                count=1,
            )
            if dry_run:
                self.logger.event(
                    "Would delete message %s.",
                    sensitive(action.message_id),
                    indent=1,
                    prefix="-",
                    extra=event,
                )
                self._log_message_detail(facts)
                return None

            self.logger.event(
                "Deleting message %s.",
                sensitive(action.message_id),
                indent=1,
                prefix="-",
                extra=event,
            )
            self._log_message_detail(facts)
            outcome = self.api.delete_message(
                channel_id=action.channel_id,
                message_id=action.message_id,
            )
            if outcome == DeleteOutcome.FAILED:
                self.logger.warning(
                    "Failed to delete message %s in channel %s.",
                    sensitive(action.message_id),
                    sensitive(action.channel_id),
                )
            return outcome

        emoji: DiscordEmoji = action.emoji or {}
        emoji_name = emoji.get("name") or "unknown"
        reaction_label = self._reaction_action_label(action)
        event = structured_event(
            "cleanup.action",
            mode="dry-run" if dry_run else "execute",
            artifact="reaction",
            action="delete",
            count=1,
            normal_count=int(action.reaction_type == ReactionType.NORMAL),
            burst_count=int(action.reaction_type == ReactionType.BURST),
        )
        if dry_run:
            self.logger.event(
                "Would delete %s from message %s.",
                reaction_label,
                sensitive(action.message_id),
                indent=1,
                prefix="-",
                extra=event,
            )
            self._log_reaction_detail(emoji_name)
            return None

        self.logger.event(
            "Deleting %s from message %s.",
            reaction_label,
            sensitive(action.message_id),
            indent=1,
            prefix="-",
            extra=event,
        )
        self._log_reaction_detail(emoji_name)
        outcome = self.api.delete_own_reaction(
            channel_id=action.channel_id,
            message_id=action.message_id,
            emoji=emoji,
            reaction_type=action.reaction_type,
        )
        if outcome == DeleteOutcome.FAILED:
            self.logger.warning(
                "Failed to delete reaction %s on message %s in channel %s.",
                sensitive(emoji_name, full=True),
                sensitive(action.message_id),
                sensitive(action.channel_id),
            )
        return outcome

    def _execute_reaction_actions(
        self,
        actions: list[PlannedAction],
        dry_run: bool,
        resume_archived_thread: Optional[Callable[[], bool]] = None,
    ) -> _DeleteActionCounts:
        if not actions:
            return _DeleteActionCounts()

        message_id = actions[0].message_id
        emoji_names = [self._reaction_detail(action) for action in actions]
        reaction_count = len(actions)
        normal_count = sum(
            action.reaction_type == ReactionType.NORMAL for action in actions
        )
        burst_count = reaction_count - normal_count
        reaction_label = (
            self._reaction_action_label(actions[0])
            if reaction_count == 1
            else f"{reaction_count} reactions"
        )
        event = structured_event(
            "cleanup.action",
            mode="dry-run" if dry_run else "execute",
            artifact="reaction",
            action="delete",
            count=reaction_count,
            normal_count=normal_count,
            burst_count=burst_count,
        )

        if dry_run:
            self.logger.event(
                "Would delete %s from message %s.",
                reaction_label,
                sensitive(message_id),
                indent=1,
                prefix="-",
                extra=event,
            )
            self._log_reaction_detail(emoji_names)
            return _DeleteActionCounts(
                deleted=reaction_count,
                processed=reaction_count,
            )

        self.logger.event(
            "Deleting %s from message %s.",
            reaction_label,
            sensitive(message_id),
            indent=1,
            prefix="-",
            extra=event,
        )
        self._log_reaction_detail(emoji_names)

        outcomes = _DeleteActionCounts()
        for action in actions:
            emoji: DiscordEmoji = action.emoji or {}
            emoji_name = emoji.get("name") or "unknown"
            outcome = self.api.delete_own_reaction(
                channel_id=action.channel_id,
                message_id=action.message_id,
                emoji=emoji,
                reaction_type=action.reaction_type,
            )
            if (
                outcome == DeleteOutcome.THREAD_ARCHIVED
                and resume_archived_thread is not None
                and resume_archived_thread()
            ):
                outcome = self.api.delete_own_reaction(
                    channel_id=action.channel_id,
                    message_id=action.message_id,
                    emoji=emoji,
                    reaction_type=action.reaction_type,
                )
            outcomes.record(outcome)
            if outcome == DeleteOutcome.FAILED:
                self.logger.warning(
                    "Failed to delete reaction %s on message %s in channel %s.",
                    sensitive(emoji_name, full=True),
                    sensitive(action.message_id),
                    sensitive(action.channel_id),
                )
            if outcome == DeleteOutcome.THREAD_ARCHIVED:
                break

        return outcomes

    @staticmethod
    def _reaction_action_label(action: PlannedAction) -> str:
        return (
            "Super Reaction"
            if action.reaction_type == ReactionType.BURST
            else "reaction"
        )

    @staticmethod
    def _reaction_detail(action: PlannedAction) -> str:
        name = (action.emoji or {}).get("name") or "unknown"
        return (
            f"{name} (super)"
            if action.reaction_type == ReactionType.BURST
            else name
        )

    def _log_message_detail(self, facts: Optional[MessageFacts]) -> None:
        if not facts:
            return
        content = (facts.message.get("content") or "").strip()
        if content:
            normalized = " ".join(content.split())
            if len(normalized) > 120:
                normalized = f"{normalized[:117]}..."
            self.logger.detail(
                "Content: %s",
                sensitive(normalized, full=True),
                indent=2,
                no_wrap=True,
            )

    def _log_reaction_detail(self, emoji_names: str | list[str]) -> None:
        names = [emoji_names] if isinstance(emoji_names, str) else emoji_names
        rendered = ", ".join(
            str(sensitive(name, full=True))
            for name in names
        )
        label = "Reaction" if len(names) == 1 else "Reactions"
        self.logger.detail(
            f"{label}: %s",
            rendered,
            indent=2,
            no_wrap=True,
        )
