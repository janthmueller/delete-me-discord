import logging
import stat

import pytest

from delete_me_discord.models import UpdateOutcome
from delete_me_discord.thread_cleanup import (
    ADMINISTRATOR_PERMISSION,
    MANAGE_THREADS_PERMISSION,
    ArchivedThreadAssessment,
    ArchivedThreadCoordinator,
    ThreadRestorationJournal,
    ThreadRestoreOutcome,
    effective_channel_permissions,
)
from delete_me_discord.utils import ResourceUnavailable


def resolve_permissions(
    *,
    base=0,
    roles=(),
    overwrites=None,
    owner=False,
):
    return effective_channel_permissions(
        guild_id="guild",
        guild_owner=owner,
        guild_permissions=str(base),
        member_role_ids=roles,
        user_id="me",
        permission_overwrites=[] if overwrites is None else overwrites,
    )


def overwrite(target_id, target_type, *, allow=0, deny=0):
    return {
        "id": target_id,
        "type": target_type,
        "allow": str(allow),
        "deny": str(deny),
    }


def test_guild_owner_and_administrator_bypass_channel_overwrites():
    denied = [
        overwrite("guild", 0, deny=MANAGE_THREADS_PERMISSION),
        overwrite("me", 1, deny=MANAGE_THREADS_PERMISSION),
    ]

    owner_permissions = resolve_permissions(owner=True, overwrites=denied)
    administrator_permissions = resolve_permissions(
        base=ADMINISTRATOR_PERMISSION,
        overwrites=denied,
    )

    assert owner_permissions & MANAGE_THREADS_PERMISSION
    assert administrator_permissions & MANAGE_THREADS_PERMISSION


def test_permission_overwrites_follow_everyone_role_member_precedence():
    permissions = resolve_permissions(
        base=MANAGE_THREADS_PERMISSION,
        roles=("member-role",),
        overwrites=[
            overwrite("guild", 0, deny=MANAGE_THREADS_PERMISSION),
            overwrite("member-role", 0, allow=MANAGE_THREADS_PERMISSION),
            overwrite("me", 1, deny=MANAGE_THREADS_PERMISSION),
        ],
    )

    assert permissions is not None
    assert not permissions & MANAGE_THREADS_PERMISSION


def test_member_allow_overrides_aggregated_role_deny():
    permissions = resolve_permissions(
        roles=("member-role",),
        overwrites=[
            overwrite("member-role", 0, deny=MANAGE_THREADS_PERMISSION),
            overwrite("me", 1, allow=MANAGE_THREADS_PERMISSION),
        ],
    )

    assert permissions is not None
    assert permissions & MANAGE_THREADS_PERMISSION


@pytest.mark.parametrize(
    "overwrites",
    [
        None,
        [{"id": "guild", "type": 0, "allow": "bad", "deny": "0"}],
        [{"id": "guild", "type": 7, "allow": "0", "deny": "0"}],
    ],
)
def test_permission_resolution_is_unknown_for_incomplete_payloads(overwrites):
    value = effective_channel_permissions(
        guild_id="guild",
        guild_owner=False,
        guild_permissions="0",
        member_role_ids=(),
        user_id="me",
        permission_overwrites=overwrites,
    )

    assert value is None


def test_restoration_journal_is_private_and_user_scoped(tmp_path):
    path = tmp_path / "thread-restoration.json"
    journal = ThreadRestorationJournal(str(path))

    journal.record("me", "thread-1")
    journal.record("other", "thread-2")
    journal.record("me", "thread-1")

    assert journal.pending("me") == ("thread-1",)
    assert journal.pending("other") == ("thread-2",)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    journal.clear("me", "thread-1")
    assert journal.pending("me") == ()
    assert journal.pending("other") == ("thread-2",)


class CoordinatorAPI:
    def __init__(self, *, roles=(), update_outcomes=(), current_channel=None):
        self.roles = list(roles)
        self.update_outcomes = list(update_outcomes)
        self.current_channel = current_channel
        self.member_calls = []
        self.archive_calls = []
        self.get_channel_calls = []

    def get_current_guild_member(self, guild_id):
        self.member_calls.append(guild_id)
        return {"roles": self.roles}

    def set_thread_archived(self, thread_id, *, archived):
        self.archive_calls.append((thread_id, archived))
        return self.update_outcomes.pop(0)

    def get_channel(self, thread_id):
        self.get_channel_calls.append(thread_id)
        if isinstance(self.current_channel, Exception):
            raise self.current_channel
        return self.current_channel


def coordinator(api, journal=None, clock=None):
    kwargs = {
        "api": api,
        "user_id": "me",
        "journal": journal,
        "logger": logging.getLogger("thread-cleanup-test"),
    }
    if clock is not None:
        kwargs["clock"] = clock
    return ArchivedThreadCoordinator(
        **kwargs,
    )


def thread(*, owner_id="me", locked=False, auto_archive_duration=60):
    metadata = {
        "archived": True,
        "locked": locked,
    }
    if auto_archive_duration is not None:
        metadata["auto_archive_duration"] = auto_archive_duration
    return {
        "id": "thread",
        "type": 11,
        "owner_id": owner_id,
        "thread_metadata": metadata,
    }


def guild(*, permissions=0, owner=False):
    return {
        "id": "guild",
        "permissions": str(permissions),
        "owner": owner,
    }


def parent(overwrites=None):
    return {
        "id": "parent",
        "type": 0,
        "permission_overwrites": [] if overwrites is None else overwrites,
    }


def test_temporary_mode_accepts_unlocked_creator_without_permission_lookup():
    api = CoordinatorAPI()

    assessment = coordinator(api).assess(
        channel=thread(),
        guild=None,
        parent=None,
        mode="temporary",
    )

    assert assessment.should_scan is True
    assert assessment.restore_expected is True
    assert api.member_calls == []


def test_temporary_mode_resolves_manage_threads_once_per_guild():
    api = CoordinatorAPI(roles=("moderator",))
    manager = coordinator(api)
    role_allow = [
        overwrite("moderator", 0, allow=MANAGE_THREADS_PERMISSION),
    ]

    first = manager.assess(
        channel=thread(owner_id="other"),
        guild=guild(),
        parent=parent(role_allow),
        mode="temporary",
    )
    second = manager.assess(
        channel=thread(owner_id="another"),
        guild=guild(),
        parent=parent(role_allow),
        mode="temporary",
    )

    assert first.should_scan is True
    assert first.restore_expected is True
    assert second.should_scan is True
    assert api.member_calls == ["guild"]


def test_locked_thread_requires_manage_threads_even_for_creator():
    api = CoordinatorAPI(roles=())

    assessment = coordinator(api).assess(
        channel=thread(locked=True),
        guild=guild(),
        parent=parent(),
        mode="temporary",
    )

    assert assessment.should_scan is False
    assert assessment.reason == "locked thread requires MANAGE_THREADS"


def test_allow_active_scans_unlocked_noncreator_without_restoration_rights():
    api = CoordinatorAPI(roles=())

    assessment = coordinator(api).assess(
        channel=thread(owner_id="other"),
        guild=guild(),
        parent=parent(),
        mode="allow-active",
    )

    assert assessment.should_scan is True
    assert assessment.restore_expected is False
    assert assessment.restoration_status == "unavailable"


def test_activation_journals_before_open_and_clears_after_restore(tmp_path):
    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    api = CoordinatorAPI(
        update_outcomes=(UpdateOutcome.APPLIED, UpdateOutcome.APPLIED),
    )
    manager = coordinator(api, journal)
    assessment = ArchivedThreadAssessment(
        should_scan=True,
        restore_expected=True,
        restoration_status="available",
    )

    activation = manager.activate(thread(), assessment)
    assert activation.opened is True
    assert journal.pending("me") == ("thread",)

    outcome = manager.restore(thread(), activation)

    assert outcome == ThreadRestoreOutcome.RESTORED
    assert api.archive_calls == [("thread", False), ("thread", True)]
    assert journal.pending("me") == ()


def test_failed_expected_restoration_retains_recovery_entry(tmp_path):
    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    api = CoordinatorAPI(
        update_outcomes=(UpdateOutcome.APPLIED, UpdateOutcome.FAILED),
    )
    manager = coordinator(api, journal)
    assessment = ArchivedThreadAssessment(
        should_scan=True,
        restore_expected=True,
        restoration_status="available",
    )

    activation = manager.activate(thread(), assessment)
    outcome = manager.restore(thread(), activation)

    assert outcome == ThreadRestoreOutcome.LEFT_ACTIVE
    assert journal.pending("me") == ("thread",)


def test_pending_recovery_is_retried_and_cleared(tmp_path):
    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    journal.record("me", "thread")
    api = CoordinatorAPI(update_outcomes=(UpdateOutcome.APPLIED,))
    manager = coordinator(api, journal)

    restored, failed = manager.restore_pending()

    assert (restored, failed) == (1, 0)
    assert api.archive_calls == [("thread", True)]
    assert journal.pending("me") == ()


@pytest.mark.parametrize("locked", [False, True])
def test_likely_auto_archive_is_reopened_when_thread_state_is_unchanged(
    tmp_path,
    locked,
):
    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    channel = thread(locked=locked)
    api = CoordinatorAPI(
        update_outcomes=(UpdateOutcome.APPLIED, UpdateOutcome.APPLIED),
        current_channel=channel,
    )
    clock_values = iter((100.0, 3700.0, 3701.0))
    manager = coordinator(api, journal, clock=clock_values.__next__)
    assessment = ArchivedThreadAssessment(
        should_scan=True,
        restore_expected=True,
        restoration_status="available",
    )

    activation = manager.activate(channel, assessment)
    result = manager.resume_after_likely_auto_archive(channel, activation)

    assert result.retry_action is True
    assert result.activation.opened is True
    assert result.activation.activated_at == 3701.0
    assert result.activation.locked is locked
    assert api.get_channel_calls == ["thread"]
    assert api.archive_calls == [("thread", False), ("thread", False)]
    assert journal.pending("me") == ("thread",)


def test_early_archive_is_treated_as_external_and_not_reopened(tmp_path):
    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    channel = thread()
    api = CoordinatorAPI(
        update_outcomes=(UpdateOutcome.APPLIED,),
        current_channel=channel,
    )
    clock_values = iter((100.0, 200.0))
    manager = coordinator(api, journal, clock=clock_values.__next__)
    assessment = ArchivedThreadAssessment(
        should_scan=True,
        restore_expected=True,
        restoration_status="available",
    )

    activation = manager.activate(channel, assessment)
    result = manager.resume_after_likely_auto_archive(channel, activation)

    assert result.retry_action is False
    assert result.activation.opened is False
    assert api.archive_calls == [("thread", False)]
    assert journal.pending("me") == ()


@pytest.mark.parametrize(
    "current_channel",
    [
        thread(locked=True),
        thread(auto_archive_duration=1440),
        thread(auto_archive_duration=None),
    ],
    ids=("lock-changed", "duration-changed", "duration-missing"),
)
def test_changed_or_unknown_auto_archive_state_is_not_reopened(
    tmp_path,
    current_channel,
):
    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    original = thread()
    api = CoordinatorAPI(
        update_outcomes=(UpdateOutcome.APPLIED,),
        current_channel=current_channel,
    )
    manager = coordinator(api, journal, clock=lambda: 3700.0)
    assessment = ArchivedThreadAssessment(
        should_scan=True,
        restore_expected=True,
        restoration_status="available",
    )

    activation = manager.activate(original, assessment)
    result = manager.resume_after_likely_auto_archive(original, activation)

    assert result.retry_action is False
    assert result.activation.opened is False
    assert api.archive_calls == [("thread", False)]
    assert journal.pending("me") == ()


def test_unknown_original_auto_archive_deadline_is_not_reopened(tmp_path):
    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    channel = thread(auto_archive_duration=None)
    api = CoordinatorAPI(
        update_outcomes=(UpdateOutcome.APPLIED,),
        current_channel=channel,
    )
    manager = coordinator(api, journal, clock=lambda: 100.0)
    assessment = ArchivedThreadAssessment(
        should_scan=True,
        restore_expected=True,
        restoration_status="available",
    )

    activation = manager.activate(channel, assessment)
    result = manager.resume_after_likely_auto_archive(channel, activation)

    assert result.retry_action is False
    assert result.activation.opened is False
    assert journal.pending("me") == ()


def test_failed_likely_auto_archive_reactivation_stays_archived(tmp_path):
    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    channel = thread()
    api = CoordinatorAPI(
        update_outcomes=(UpdateOutcome.APPLIED, UpdateOutcome.FAILED),
        current_channel=channel,
    )
    clock_values = iter((100.0, 3700.0))
    manager = coordinator(api, journal, clock=clock_values.__next__)
    assessment = ArchivedThreadAssessment(
        should_scan=True,
        restore_expected=True,
        restoration_status="available",
    )

    activation = manager.activate(channel, assessment)
    result = manager.resume_after_likely_auto_archive(channel, activation)

    assert result.retry_action is False
    assert result.activation.opened is False
    assert api.archive_calls == [("thread", False), ("thread", False)]
    assert journal.pending("me") == ()


def test_failed_state_refresh_keeps_activation_recoverable(tmp_path):
    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    channel = thread()
    api = CoordinatorAPI(
        update_outcomes=(UpdateOutcome.APPLIED,),
        current_channel=ResourceUnavailable("unavailable", status_code=403),
    )
    manager = coordinator(api, journal, clock=lambda: 100.0)
    assessment = ArchivedThreadAssessment(
        should_scan=True,
        restore_expected=True,
        restoration_status="available",
    )

    activation = manager.activate(channel, assessment)
    result = manager.resume_after_likely_auto_archive(channel, activation)

    assert result.retry_action is False
    assert result.activation.opened is True
    assert journal.pending("me") == ("thread",)
