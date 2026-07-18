from datetime import datetime, timedelta, timezone

import pytest

from delete_me_discord.cleanup import (
    ActionKind,
    ChannelCleanupStats,
    CleanupPlanner,
    CleanupPolicy,
    CleanupRunStats,
)
from delete_me_discord.type_enums import ReactionType


class MessageType:
    def __init__(self, deletable: bool):
        self.deletable = deletable


def message(
    message_id: str,
    author_id: str,
    sent_at: datetime,
    *,
    deletable: bool = True,
    reactions=None,
):
    return {
        "message_id": message_id,
        "timestamp": sent_at.isoformat().replace("+00:00", "Z"),
        "channel_id": "channel",
        "type": MessageType(deletable),
        "author_id": author_id,
        "author_username": None,
        "content": None,
        "reactions": reactions or [],
    }


def planner(
    cutoff_time: datetime,
    *,
    preserve_n: int = 0,
    preserve_n_mode: str = "all",
    delete_reactions: bool = False,
) -> CleanupPlanner:
    return CleanupPlanner(
        user_id="me",
        policy=CleanupPolicy(
            cutoff_time=cutoff_time,
            preserve_n=preserve_n,
            preserve_n_mode=preserve_n_mode,
            delete_reactions=delete_reactions,
        ),
    )


def test_owned_deletable_message_plans_one_parent_delete():
    now = datetime.now(timezone.utc)
    plan = planner(now, delete_reactions=True).build_channel_plan(
        [
            message(
                "1",
                "me",
                now - timedelta(days=1),
                reactions=[
                    {
                        "count": 1,
                        "count_details": {"normal": 1, "burst": 0},
                        "me": True,
                        "me_burst": False,
                        "emoji": {"name": "wave"},
                    }
                ],
            )
        ]
    )

    assert [action.kind for action in plan.actions] == [ActionKind.DELETE_MESSAGE]


def test_foreign_message_plans_owned_normal_and_super_reactions():
    now = datetime.now(timezone.utc)
    plan = planner(now, delete_reactions=True).build_channel_plan(
        [
            message(
                "1",
                "other",
                now - timedelta(days=1),
                deletable=False,
                reactions=[
                    {
                        "me": True,
                        "me_burst": True,
                        "emoji": {"name": "wave"},
                    }
                ],
            )
        ]
    )

    assert [action.kind for action in plan.actions] == [
        ActionKind.DELETE_REACTION,
        ActionKind.DELETE_REACTION,
    ]
    assert [action.reaction_type for action in plan.actions] == [
        ReactionType.NORMAL,
        ReactionType.BURST,
    ]


def test_preserve_n_all_counts_foreign_messages():
    now = datetime.now(timezone.utc)
    plan = planner(now, preserve_n=1, preserve_n_mode="all").build_channel_plan(
        [
            message("2", "other", now - timedelta(days=1), deletable=False),
            message("1", "me", now - timedelta(days=2)),
        ]
    )

    assert plan.action_count == 1
    assert plan.actions[0].message_id == "1"


def test_preserve_n_mine_counts_only_owned_deletable_messages():
    now = datetime.now(timezone.utc)
    plan = planner(now, preserve_n=1, preserve_n_mode="mine").build_channel_plan(
        [
            message("2", "other", now - timedelta(days=1), deletable=False),
            message("1", "me", now - timedelta(days=2)),
        ]
    )

    assert plan.action_count == 0
    assert plan.decisions[1].preserve_message is True


def test_time_window_preserves_owned_reaction_on_foreign_message():
    now = datetime.now(timezone.utc)
    plan = planner(now - timedelta(days=1), delete_reactions=True).build_channel_plan(
        [
            message(
                "1",
                "other",
                now,
                deletable=False,
                reactions=[
                    {
                        "me": True,
                        "emoji": {"name": "wave"},
                    }
                ],
            )
        ]
    )

    assert plan.action_count == 0
    assert plan.decisions[0].preserve_reaction_count == 1


@pytest.mark.parametrize(
    ("preserve_n", "preserve_n_mode"),
    [(-1, "all"), (0, "invalid")],
)
def test_cleanup_policy_rejects_invalid_retention(
    preserve_n,
    preserve_n_mode,
):
    with pytest.raises(ValueError):
        CleanupPolicy(
            cutoff_time=datetime.now(timezone.utc),
            preserve_n=preserve_n,
            preserve_n_mode=preserve_n_mode,
        )


def test_channel_stats_preserve_mapping_reads_for_reporting_boundaries():
    stats = ChannelCleanupStats(
        deleted_count=2,
        reactions_removed_count=3,
    )

    assert stats.deleted_count == 2
    assert stats["deleted_count"] == 2
    assert stats.get("reactions_removed_count") == 3
    assert dict(stats)["failed_count"] == 0
    with pytest.raises(KeyError):
        _ = stats["unknown"]


def test_run_stats_merge_channel_and_thread_deltas():
    total = CleanupRunStats()
    total.add_channel_stats(
        ChannelCleanupStats(
            deleted_count=2,
            absent_count=1,
            foreign_reactions_normal_count=3,
            thread_state_interrupted_count=1,
        )
    )
    total.merge(
        CleanupRunStats(
            threads_deleted_count=1,
            archived_threads_restored_count=1,
        )
    )

    assert total.deleted_count == 2
    assert total.absent_count == 1
    assert total.foreign_reactions_normal_count == 3
    assert total.archived_threads_interrupted_count == 1
    assert total.threads_deleted_count == 1
    assert total.archived_threads_restored_count == 1
