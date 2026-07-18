from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Iterator

from ..discord.models import DiscordMessage, DiscordReaction
from ..discord.type_enums import ReactionType
from .models import (
    ActionKind,
    ChannelPlan,
    ForeignReactionImpact,
    MessageDecision,
    MessageFacts,
    OwnedReaction,
    PlannedAction,
)


@dataclass(frozen=True, slots=True)
class CleanupPolicy:
    """Message retention and reaction cleanup settings used by the planner."""

    cutoff_time: datetime
    preserve_n: int = 0
    preserve_n_mode: str = "all"
    delete_reactions: bool = False

    def __post_init__(self) -> None:
        if self.preserve_n < 0:
            raise ValueError("preserve_n must not be negative.")
        if self.preserve_n_mode not in {"mine", "all"}:
            raise ValueError("preserve_n_mode must be 'mine' or 'all'.")


class CleanupPlanner:
    """Convert normalized Discord messages into deterministic cleanup actions."""

    def __init__(self, user_id: str, policy: CleanupPolicy):
        self.user_id = user_id
        self.policy = policy

    def build_channel_plan(self, messages: Iterable[DiscordMessage]) -> ChannelPlan:
        return ChannelPlan(decisions=tuple(self.iter_message_decisions(messages)))

    def iter_message_decisions(
        self,
        messages: Iterable[DiscordMessage],
    ) -> Iterator[MessageDecision]:
        preserve_n_count = 0
        preserve_count_active = self.policy.preserve_n > 0

        for message in messages:
            facts = self.build_message_facts(message)
            if preserve_count_active and (
                self.policy.preserve_n_mode == "all" or facts.is_deletable
            ):
                preserve_n_count += 1

            in_count_window = (
                preserve_count_active
                and preserve_n_count <= self.policy.preserve_n
            )
            yield self.build_message_decision(
                facts=facts,
                in_preserve_window=(
                    in_count_window
                    or facts.message_time >= self.policy.cutoff_time
                ),
            )

    def build_message_facts(self, message: DiscordMessage) -> MessageFacts:
        message_time = datetime.fromisoformat(
            message["timestamp"].replace("Z", "+00:00")
        )
        is_author = message["author_id"] == self.user_id
        is_deletable = is_author and bool(
            getattr(message["type"], "deletable", False)
        )
        reactions = message.get("reactions") or []
        my_reactions: list[OwnedReaction] = []
        if self.policy.delete_reactions:
            for reaction in reactions:
                emoji = reaction.get("emoji") or {}
                if reaction.get("me"):
                    my_reactions.append(
                        OwnedReaction(
                            emoji=emoji,
                            reaction_type=ReactionType.NORMAL,
                        )
                    )
                if reaction.get("me_burst"):
                    my_reactions.append(
                        OwnedReaction(
                            emoji=emoji,
                            reaction_type=ReactionType.BURST,
                        )
                    )
        return MessageFacts(
            message=message,
            message_time=message_time,
            is_author=is_author,
            is_deletable=is_deletable,
            my_reactions=tuple(my_reactions),
            foreign_reaction_impact=self.foreign_reaction_impact(reactions),
        )

    @staticmethod
    def foreign_reaction_impact(
        reactions: Iterable[DiscordReaction],
    ) -> ForeignReactionImpact:
        impact = ForeignReactionImpact()
        for reaction in reactions:
            details = reaction.get("count_details")
            me = reaction.get("me")
            me_burst = reaction.get("me_burst")
            if (
                not isinstance(details, dict)
                or not isinstance(me, bool)
                or not isinstance(me_burst, bool)
            ):
                impact = impact.combined_with(
                    ForeignReactionImpact(complete=False)
                )
                continue

            normal = details.get("normal")
            burst = details.get("burst")
            if (
                not isinstance(normal, int)
                or isinstance(normal, bool)
                or normal < int(me)
                or not isinstance(burst, int)
                or isinstance(burst, bool)
                or burst < int(me_burst)
            ):
                impact = impact.combined_with(
                    ForeignReactionImpact(complete=False)
                )
                continue

            total = reaction.get("count")
            if (
                not isinstance(total, int)
                or isinstance(total, bool)
                or total != normal + burst
            ):
                impact = impact.combined_with(
                    ForeignReactionImpact(complete=False)
                )
                continue

            impact = impact.combined_with(
                ForeignReactionImpact(
                    normal=normal - int(me),
                    burst=burst - int(me_burst),
                )
            )
        return impact

    @staticmethod
    def build_message_decision(
        facts: MessageFacts,
        in_preserve_window: bool,
    ) -> MessageDecision:
        actions: list[PlannedAction] = []
        preserve_message = in_preserve_window and facts.is_deletable
        preserve_reactions = (
            in_preserve_window
            and not facts.is_deletable
            and bool(facts.my_reactions)
        )

        if not in_preserve_window:
            if facts.is_deletable:
                actions.append(
                    PlannedAction(
                        kind=ActionKind.DELETE_MESSAGE,
                        channel_id=facts.message["channel_id"],
                        message_id=facts.message["message_id"],
                        message_time=facts.message_time,
                    )
                )
            else:
                actions.extend(
                    PlannedAction(
                        kind=ActionKind.DELETE_REACTION,
                        channel_id=facts.message["channel_id"],
                        message_id=facts.message["message_id"],
                        message_time=facts.message_time,
                        emoji=reaction.emoji,
                        reaction_type=reaction.reaction_type,
                    )
                    for reaction in facts.my_reactions
                )

        return MessageDecision(
            facts=facts,
            preserve_message=preserve_message,
            preserve_reactions=preserve_reactions,
            actions=tuple(actions),
        )
