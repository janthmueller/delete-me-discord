from datetime import datetime, timedelta, timezone

import pytest

from delete_me_discord.cleanup.thread_recovery import (
    ActiveThreadBaseline,
    ArchiveRecoveryReason,
    evaluate_active_archive_recovery,
)
from delete_me_discord.cleanup.threads import ThreadRestorationJournal
from delete_me_discord.discord.models import UpdateOutcome
from tests._thread_cleanup_support import (
    CoordinatorAPI,
    active_thread,
    archived_snapshot,
    coordinator,
)


def test_active_thread_recovery_requires_a_known_deadline():
    activity_at = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    baseline = ActiveThreadBaseline(
        archive_timestamp=activity_at,
        last_message_timestamp=activity_at,
        auto_archive_duration_seconds=None,
        locked=False,
        pinned=False,
    )
    current = ActiveThreadBaseline(
        archive_timestamp=activity_at + timedelta(hours=1),
        last_message_timestamp=activity_at,
        auto_archive_duration_seconds=None,
        locked=False,
        pinned=False,
    )

    decision = evaluate_active_archive_recovery(baseline, current)

    assert decision.reason == ArchiveRecoveryReason.DEADLINE_UNKNOWN
    assert decision.should_reopen is False


def test_active_thread_recovery_rejects_archive_before_latest_activity():
    activity_at = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    baseline = ActiveThreadBaseline(
        archive_timestamp=activity_at,
        last_message_timestamp=activity_at,
        auto_archive_duration_seconds=3600,
        locked=False,
        pinned=False,
    )
    current = ActiveThreadBaseline(
        archive_timestamp=activity_at + timedelta(minutes=30),
        last_message_timestamp=activity_at + timedelta(minutes=31),
        auto_archive_duration_seconds=3600,
        locked=False,
        pinned=False,
    )

    decision = evaluate_active_archive_recovery(baseline, current)

    assert decision.reason == ArchiveRecoveryReason.ARCHIVE_BEFORE_ACTIVITY
    assert decision.should_reopen is False


def test_active_thread_baseline_captures_status_and_message_activity():
    status_changed_at = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    last_message_at = status_changed_at + timedelta(minutes=30)
    channel = active_thread(
        archive_timestamp=status_changed_at,
        last_message_timestamp=last_message_at,
    )

    baseline = coordinator(CoordinatorAPI()).observe_active(channel)

    assert baseline.archive_timestamp == status_changed_at
    assert baseline.last_message_timestamp == last_message_at
    assert baseline.auto_archive_duration_seconds == 3600
    assert baseline.locked is False
    assert baseline.pinned is False


@pytest.mark.parametrize(
    ("seconds_before_deadline", "should_reopen"),
    [(30, True), (31, False)],
)
def test_active_thread_recovery_uses_thirty_second_deadline_tolerance(
    tmp_path,
    seconds_before_deadline,
    should_reopen,
):
    status_changed_at = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    last_message_at = status_changed_at + timedelta(minutes=30)
    expected_deadline = last_message_at + timedelta(hours=1)
    channel = active_thread(
        archive_timestamp=status_changed_at,
        last_message_timestamp=last_message_at,
    )
    current = archived_snapshot(
        channel,
        archived_at=expected_deadline
        - timedelta(seconds=seconds_before_deadline),
    )
    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    api = CoordinatorAPI(
        update_outcomes=(
            (UpdateOutcome.APPLIED,) if should_reopen else ()
        ),
        current_channel=current,
    )
    manager = coordinator(api, journal)

    result = manager.resume_active_after_likely_auto_archive(
        channel,
        manager.observe_active(channel),
        guild=None,
        parent=None,
        mode="temporary",
    )

    assert result.retry_action is should_reopen
    assert (result.activation is not None) is should_reopen
    assert api.archive_calls == (
        [("thread", False)] if should_reopen else []
    )
    assert journal.pending("me") == (
        ("thread",) if should_reopen else ()
    )


def test_active_thread_recovery_uses_new_message_from_refreshed_state(
    tmp_path,
):
    status_changed_at = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    initial_message_at = status_changed_at + timedelta(minutes=5)
    newer_message_at = status_changed_at + timedelta(minutes=55)
    channel = active_thread(
        archive_timestamp=status_changed_at,
        last_message_timestamp=initial_message_at,
    )
    current = archived_snapshot(
        channel,
        archived_at=status_changed_at + timedelta(hours=1),
        last_message_timestamp=newer_message_at,
    )
    api = CoordinatorAPI(current_channel=current)
    manager = coordinator(
        api,
        ThreadRestorationJournal(str(tmp_path / "journal.json")),
    )

    result = manager.resume_active_after_likely_auto_archive(
        channel,
        manager.observe_active(channel),
        guild=None,
        parent=None,
        mode="temporary",
    )

    assert result.retry_action is False
    assert result.activation is None
    assert api.archive_calls == []


def test_active_thread_recovery_uses_unarchive_time_when_it_is_newer(
    tmp_path,
):
    last_message_at = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    unarchived_at = last_message_at + timedelta(minutes=30)
    channel = active_thread(
        archive_timestamp=unarchived_at,
        last_message_timestamp=last_message_at,
    )
    current = archived_snapshot(
        channel,
        archived_at=unarchived_at + timedelta(hours=1),
    )
    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    api = CoordinatorAPI(
        update_outcomes=(UpdateOutcome.APPLIED,),
        current_channel=current,
    )
    manager = coordinator(api, journal)

    result = manager.resume_active_after_likely_auto_archive(
        channel,
        manager.observe_active(channel),
        guild=None,
        parent=None,
        mode="temporary",
    )

    assert result.retry_action is True
    assert result.activation is not None
    assert api.archive_calls == [("thread", False)]


def test_active_pinned_thread_is_never_treated_as_auto_archived(tmp_path):
    status_changed_at = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    channel = active_thread(
        archive_timestamp=status_changed_at,
        last_message_timestamp=status_changed_at,
        flags=1 << 1,
    )
    current = archived_snapshot(
        channel,
        archived_at=status_changed_at + timedelta(hours=1),
    )
    api = CoordinatorAPI(current_channel=current)
    manager = coordinator(
        api,
        ThreadRestorationJournal(str(tmp_path / "journal.json")),
    )

    result = manager.resume_active_after_likely_auto_archive(
        channel,
        manager.observe_active(channel),
        guild=None,
        parent=None,
        mode="temporary",
    )

    assert result.retry_action is False
    assert result.activation is None
    assert api.archive_calls == []


def test_active_thread_recovery_stops_when_thread_becomes_pinned(tmp_path):
    status_changed_at = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    channel = active_thread(
        archive_timestamp=status_changed_at,
        last_message_timestamp=status_changed_at,
    )
    current = archived_snapshot(
        channel,
        archived_at=status_changed_at + timedelta(hours=1),
    )
    current["flags"] = 1 << 1
    api = CoordinatorAPI(current_channel=current)
    manager = coordinator(
        api,
        ThreadRestorationJournal(str(tmp_path / "journal.json")),
    )

    result = manager.resume_active_after_likely_auto_archive(
        channel,
        manager.observe_active(channel),
        guild=None,
        parent=None,
        mode="temporary",
    )

    assert result.retry_action is False
    assert result.activation is None
    assert api.archive_calls == []


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("locked", True),
        ("auto_archive_duration", 1440),
    ],
)
def test_active_thread_recovery_stops_when_state_contract_changes(
    tmp_path,
    changed_field,
    changed_value,
):
    status_changed_at = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    channel = active_thread(
        archive_timestamp=status_changed_at,
        last_message_timestamp=status_changed_at,
    )
    current = archived_snapshot(
        channel,
        archived_at=status_changed_at + timedelta(hours=1),
    )
    current["thread_metadata"][changed_field] = changed_value
    api = CoordinatorAPI(current_channel=current)
    manager = coordinator(
        api,
        ThreadRestorationJournal(str(tmp_path / "journal.json")),
    )

    result = manager.resume_active_after_likely_auto_archive(
        channel,
        manager.observe_active(channel),
        guild=None,
        parent=None,
        mode="temporary",
    )

    assert result.retry_action is False
    assert result.activation is None
    assert api.archive_calls == []


@pytest.mark.parametrize(
    "missing_field",
    ["initial_archive_timestamp", "initial_message_id", "current_message_id"],
)
def test_active_thread_recovery_fails_closed_without_activity_timestamp(
    tmp_path,
    missing_field,
):
    status_changed_at = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    channel = active_thread(
        archive_timestamp=status_changed_at,
        last_message_timestamp=status_changed_at,
    )
    current = archived_snapshot(
        channel,
        archived_at=status_changed_at + timedelta(hours=1),
    )
    if missing_field == "initial_archive_timestamp":
        del channel["thread_metadata"]["archive_timestamp"]
    elif missing_field == "initial_message_id":
        del channel["last_message_id"]
    else:
        del current["last_message_id"]
    api = CoordinatorAPI(current_channel=current)
    manager = coordinator(
        api,
        ThreadRestorationJournal(str(tmp_path / "journal.json")),
    )

    result = manager.resume_active_after_likely_auto_archive(
        channel,
        manager.observe_active(channel),
        guild=None,
        parent=None,
        mode="temporary",
    )

    assert result.retry_action is False
    assert result.activation is None
    assert api.archive_calls == []


def test_active_thread_recovery_uses_allow_active_only_when_requested():
    status_changed_at = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    channel = active_thread(
        archive_timestamp=status_changed_at,
        last_message_timestamp=status_changed_at,
        owner_id="other",
    )
    current = archived_snapshot(
        channel,
        archived_at=status_changed_at + timedelta(hours=1),
    )

    strict_api = CoordinatorAPI(current_channel=current)
    strict_manager = coordinator(strict_api)
    strict = strict_manager.resume_active_after_likely_auto_archive(
        channel,
        strict_manager.observe_active(channel),
        guild=None,
        parent=None,
        mode="temporary",
    )

    permissive_api = CoordinatorAPI(
        update_outcomes=(UpdateOutcome.APPLIED,),
        current_channel=current,
    )
    permissive_manager = coordinator(permissive_api)
    permissive = permissive_manager.resume_active_after_likely_auto_archive(
        channel,
        permissive_manager.observe_active(channel),
        guild=None,
        parent=None,
        mode="allow-active",
    )

    assert strict.retry_action is False
    assert strict_api.archive_calls == []
    assert permissive.retry_action is True
    assert permissive.activation is not None
    assert permissive.activation.restore_expected is False
    assert permissive_api.archive_calls == [("thread", False)]
