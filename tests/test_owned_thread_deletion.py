import logging

import pytest

from delete_me_discord.cleanup.thread_deletion import (
    OwnedThreadDeletionCoordinator,
)
from delete_me_discord.discord.models import DeleteOutcome


class ThreadDeletionAPI:
    def __init__(
        self,
        *,
        delete_outcome=DeleteOutcome.DELETED,
        scan_complete=True,
    ):
        self.delete_outcome = delete_outcome
        self.scan_complete = scan_complete
        self.deleted_threads = []

    def delete_thread(self, thread_id):
        self.deleted_threads.append(thread_id)
        return self.delete_outcome

    def get_last_fetch_summary(self, _channel_id):
        return {"complete": self.scan_complete}


def thread(*, owner_id="me", message_count=1, channel_type=11):
    return {
        "id": "thread-1",
        "type": channel_type,
        "owner_id": owner_id,
        "message_count": message_count,
    }


def message(message_id, author_id, reactions=None):
    return {
        "message_id": message_id,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "channel_id": "thread-1",
        "type": 0,
        "author_id": author_id,
        "author_username": author_id,
        "content": None,
        "reactions": reactions or [],
    }


def coordinator(api, impacts):
    return OwnedThreadDeletionCoordinator(
        api=api,
        user_id="me",
        logger=logging.getLogger("owned-thread-deletion-test"),
        report_impact=lambda channel, impact: impacts.append((channel, impact)),
    )


@pytest.mark.parametrize(
    ("mode", "channel"),
    [
        ("none", thread()),
        ("all", thread(channel_type=0)),
        ("all", thread(owner_id="other")),
    ],
)
def test_ineligible_thread_deletion_does_not_fetch_or_delete(mode, channel):
    api = ThreadDeletionAPI()
    impacts = []
    fetch_calls = []

    outcome = coordinator(api, impacts).prepare(
        channel=channel,
        mode=mode,
        dry_run=False,
        fetch_complete_history=lambda: fetch_calls.append(True) or [],
    )

    assert outcome.terminal is False
    assert api.deleted_threads == []
    assert fetch_calls == []
    assert impacts == []


def test_all_mode_deletes_immediately_without_scanning():
    api = ThreadDeletionAPI()
    impacts = []
    fetch_calls = []

    outcome = coordinator(api, impacts).prepare(
        channel=thread(),
        mode="all",
        dry_run=False,
        fetch_complete_history=lambda: fetch_calls.append(True) or [],
    )

    assert outcome.terminal is True
    assert outcome.deleted is True
    assert api.deleted_threads == ["thread-1"]
    assert fetch_calls == []
    assert impacts == []


def test_all_mode_dry_run_scans_and_reports_foreign_impact():
    api = ThreadDeletionAPI()
    impacts = []
    messages = [
        message("mine", "me"),
        message(
            "theirs",
            "other",
            reactions=[
                {
                    "count": 2,
                    "count_details": {"normal": 1, "burst": 1},
                    "me": False,
                    "me_burst": False,
                    "emoji": {"name": "wave"},
                }
            ],
        ),
    ]

    outcome = coordinator(api, impacts).prepare(
        channel=thread(message_count=2),
        mode="all",
        dry_run=True,
        fetch_complete_history=lambda: messages,
    )

    assert outcome.terminal is True
    assert outcome.planned is True
    assert outcome.impact is impacts[0][1]
    assert outcome.impact.own_messages == 1
    assert outcome.impact.foreign_messages == 1
    assert outcome.impact.foreign_reactions.normal == 1
    assert outcome.impact.foreign_reactions.burst == 1
    assert api.deleted_threads == []


def test_self_only_falls_back_with_scanned_messages_when_scan_is_incomplete():
    api = ThreadDeletionAPI(scan_complete=False)
    impacts = []
    messages = [message("mine", "me")]

    outcome = coordinator(api, impacts).prepare(
        channel=thread(),
        mode="self-only",
        dry_run=False,
        fetch_complete_history=lambda: messages,
    )

    assert outcome.terminal is False
    assert outcome.scanned_messages == tuple(messages)
    assert outcome.impact is None
    assert impacts[0][1].scan_complete is False
    assert impacts[0][1].foreign_reactions.complete is False
    assert api.deleted_threads == []


def test_self_only_deletes_after_complete_all_own_scan():
    api = ThreadDeletionAPI()
    impacts = []

    outcome = coordinator(api, impacts).prepare(
        channel=thread(),
        mode="self-only",
        dry_run=False,
        fetch_complete_history=lambda: [message("mine", "me")],
    )

    assert outcome.terminal is True
    assert outcome.deleted is True
    assert outcome.scanned_messages is None
    assert api.deleted_threads == ["thread-1"]


def test_invalid_mode_is_rejected_before_fetching():
    api = ThreadDeletionAPI()

    with pytest.raises(ValueError, match="delete_owned_threads"):
        coordinator(api, []).prepare(
            channel=thread(),
            mode="invalid",
            dry_run=False,
            fetch_complete_history=lambda: [],
        )
