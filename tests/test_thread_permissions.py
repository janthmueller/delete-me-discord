import pytest

from delete_me_discord.cleanup.threads import (
    ADMINISTRATOR_PERMISSION,
    MANAGE_THREADS_PERMISSION,
    effective_channel_permissions,
)
from tests._thread_cleanup_support import overwrite


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
