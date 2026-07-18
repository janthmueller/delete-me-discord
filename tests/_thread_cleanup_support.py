import logging
from datetime import datetime, timezone

from delete_me_discord.cleanup.threads import ArchivedThreadCoordinator


def overwrite(target_id, target_type, *, allow=0, deny=0):
    return {
        "id": target_id,
        "type": target_type,
        "allow": str(allow),
        "deny": str(deny),
    }


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
    return ArchivedThreadCoordinator(**kwargs)


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


def snowflake_at(value):
    discord_epoch = datetime(2015, 1, 1, tzinfo=timezone.utc)
    milliseconds = int((value - discord_epoch).total_seconds() * 1000)
    return str(milliseconds << 22)


def active_thread(
    *,
    archive_timestamp,
    last_message_timestamp,
    owner_id="me",
    locked=False,
    auto_archive_duration=60,
    flags=0,
):
    channel = thread(
        owner_id=owner_id,
        locked=locked,
        auto_archive_duration=auto_archive_duration,
    )
    channel["last_message_id"] = snowflake_at(last_message_timestamp)
    channel["flags"] = flags
    channel["thread_metadata"].update({
        "archived": False,
        "archive_timestamp": archive_timestamp.isoformat(),
    })
    return channel


def archived_snapshot(channel, *, archived_at, last_message_timestamp=None):
    current = {
        **channel,
        "thread_metadata": {
            **channel["thread_metadata"],
            "archived": True,
            "archive_timestamp": archived_at.isoformat(),
        },
    }
    if last_message_timestamp is not None:
        current["last_message_id"] = snowflake_at(last_message_timestamp)
    return current


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
