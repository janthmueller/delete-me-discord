from datetime import datetime, timedelta, timezone

import pytest

from delete_me_discord.discord.channel_types import (
    MESSAGE_CHANNEL_TYPES,
    ROOT_MESSAGE_CHANNEL_TYPES,
    THREAD_CHANNEL_TYPES,
    ChannelType,
)
from delete_me_discord.cleanup import MessageCleaner
from delete_me_discord.discovery import collect_channels_from_inventory
from delete_me_discord.discord.models import DeleteOutcome, UpdateOutcome
from delete_me_discord.scope import (
    ScopeFilter,
    ScopeInventory,
    ScopeRules,
    should_include_channel,
)
from delete_me_discord.discord.type_enums import MessageType, ReactionType
from delete_me_discord.cleanup.threads import (
    MANAGE_THREADS_PERMISSION,
    ThreadRestorationJournal,
)
from delete_me_discord.utils import PROGRESS_LEVEL


def make_inventory() -> ScopeInventory:
    return ScopeInventory(
        guilds=[{"id": "g1", "name": "Guild"}],
        root_channels=[{"id": "dm", "type": 1, "recipients": [{"username": "Amy"}]}],
        guild_channels_by_guild={
            "g1": [
                {"id": "cat", "type": 4, "name": "Category"},
                {"id": "text", "type": 0, "name": "Text", "parent_id": "cat"},
                {"id": "voice", "type": 2, "name": "Voice", "parent_id": "cat"},
                {"id": "news", "type": 5, "name": "News", "parent_id": "cat"},
                {"id": "stage", "type": 13, "name": "Stage", "parent_id": "cat"},
                {"id": "forum", "type": 15, "name": "Forum", "parent_id": "cat"},
            ],
        },
        threads_by_guild={
            "g1": [
                {
                    "id": "active-thread",
                    "type": 11,
                    "name": "Active thread",
                    "guild_id": "g1",
                    "parent_id": "text",
                    "category_id": "cat",
                    "thread_metadata": {"archived": False},
                },
                {
                    "id": "archived-post",
                    "type": 11,
                    "name": "Archived post",
                    "guild_id": "g1",
                    "parent_id": "forum",
                    "category_id": "cat",
                    "thread_metadata": {"archived": True},
                },
            ],
        },
    )


def make_owned_thread_inventory(
    *,
    owner_id: str = "me",
    message_count: int = 1,
) -> ScopeInventory:
    inventory = make_inventory()
    thread = dict(inventory.threads_by_guild["g1"][0])
    thread["owner_id"] = owner_id
    thread["message_count"] = message_count
    inventory.threads_by_guild["g1"] = [thread]
    return inventory


def make_thread_message(
    message_id: str,
    author_id: str,
    *,
    reactions=None,
):
    return {
        "message_id": message_id,
        "timestamp": "2020-01-01T00:00:00+00:00",
        "channel_id": "active-thread",
        "type": MessageType.DEFAULT,
        "author_id": author_id,
        "author_username": author_id,
        "content": "old",
        "reactions": reactions or [],
    }


class ThreadCleanupAPI:
    def __init__(
        self,
        messages,
        *,
        complete: bool = True,
        delete_thread_result: DeleteOutcome = DeleteOutcome.DELETED,
    ):
        self.messages = list(messages)
        self.complete = complete
        self.delete_thread_result = delete_thread_result
        self.fetch_calls = []
        self.deleted_threads = []
        self.deleted_messages = []
        self.deleted_reactions = []

    def fetch_messages(self, channel_id, **kwargs):
        self.fetch_calls.append((channel_id, kwargs))
        yield from self.messages

    def get_last_fetch_summary(self, channel_id):
        return {
            "fetched_count": len(self.messages),
            "stop_reason": "exhausted channel history",
            "wait_count": 0,
            "waited_seconds": 0.0,
            "complete": self.complete,
        }

    def delete_thread(self, thread_id):
        self.deleted_threads.append(thread_id)
        return self.delete_thread_result

    def delete_message(self, channel_id, message_id):
        self.deleted_messages.append((channel_id, message_id))
        return DeleteOutcome.DELETED

    def delete_own_reaction(
        self,
        channel_id,
        message_id,
        emoji,
        reaction_type=ReactionType.NORMAL,
    ):
        self.deleted_reactions.append(
            (channel_id, message_id, emoji, reaction_type)
        )
        return DeleteOutcome.DELETED


def test_discovery_renders_threads_in_their_category_with_parent_context():
    data = collect_channels_from_inventory(make_inventory(), include_set=set(), exclude_set=set())

    categories = data["guilds"][0]["categories"]
    assert [category["id"] for category in categories] == ["cat"]
    entries = {entry["id"]: entry for entry in categories[0]["channels"]}
    assert entries["forum"]["type"] == "GuildForum"
    assert entries["active-thread"] == {
        "id": "active-thread",
        "name": "Active thread",
        "type": "PublicThread",
        "parent_id": "text",
        "parent_name": "Text",
        "archived": False,
    }
    assert entries["archived-post"]["parent_name"] == "Forum"
    assert entries["archived-post"]["archived"] is True


def test_scope_filtering_uses_channel_parent_category_and_guild_specificity():
    thread = {
        "id": "thread",
        "guild_id": "guild",
        "parent_id": "forum",
        "category_id": "category",
    }

    assert should_include_channel(
        thread,
        ScopeRules.from_values({"category"}, {"guild"}),
    )
    assert not should_include_channel(
        thread,
        ScopeRules.from_values({"category"}, {"forum"}),
    )
    assert should_include_channel(
        thread,
        ScopeRules.from_values({"forum"}, {"category"}),
    )
    assert should_include_channel(
        thread,
        ScopeRules.from_values({"thread"}, {"forum", "category", "guild"}),
    )
    assert not should_include_channel(
        thread,
        ScopeRules.from_values({"forum", "category", "guild"}, {"thread"}),
    )


def test_channel_type_exclusion_wins_over_explicit_thread_id():
    thread = {
        "id": "thread",
        "type": 12,
        "parent_id": "forum",
        "category_id": "category",
        "guild_id": "guild",
        "thread_metadata": {"archived": False},
    }
    scope_filter = ScopeFilter.from_names(["PrivateThread"])

    assert not should_include_channel(
        thread,
        ScopeRules.from_values({"thread"}),
        scope_filter=scope_filter,
    )


def test_cleaner_processes_direct_and_thread_channels_but_not_containers():
    cleaner = MessageCleaner(api=object(), user_id="me", scope_inventory=make_inventory())

    assert [channel["id"] for channel in cleaner.get_all_channels()] == [
        "dm",
        "text",
        "active-thread",
        "voice",
        "news",
        "stage",
        "archived-post",
    ]


def test_cleaner_applies_the_same_channel_type_filter_as_discovery():
    cleaner = MessageCleaner(
        api=object(),
        user_id="me",
        scope_inventory=make_inventory(),
        scope_filter=ScopeFilter.from_names(["DM", "GuildVoice", "PublicThread"]),
    )

    assert [channel["id"] for channel in cleaner.get_all_channels()] == [
        "text",
        "news",
        "stage",
    ]


@pytest.mark.parametrize("channel_type", sorted(MESSAGE_CHANNEL_TYPES, key=int))
def test_every_message_channel_type_reaches_message_and_reaction_cleanup(
    channel_type,
):
    channel_id = f"scope-{int(channel_type)}"
    channel = {
        "id": channel_id,
        "type": channel_type,
        "name": "scope",
    }
    guild_channels = []
    threads = []
    root_channels = []
    if channel_type in ROOT_MESSAGE_CHANNEL_TYPES:
        root_channels.append(channel)
    elif channel_type in THREAD_CHANNEL_TYPES:
        parent_type = (
            ChannelType.GUILD_ANNOUNCEMENT
            if channel_type == ChannelType.ANNOUNCEMENT_THREAD
            else ChannelType.GUILD_TEXT
        )
        parent = {"id": "parent", "type": parent_type, "name": "parent"}
        channel.update({
            "guild_id": "guild",
            "parent_id": parent["id"],
            "thread_metadata": {"archived": False},
        })
        guild_channels.append(parent)
        threads.append(channel)
    else:
        guild_channels.append(channel)

    inventory = ScopeInventory(
        guilds=[{"id": "guild", "name": "Guild"}] if not root_channels else [],
        root_channels=root_channels,
        guild_channels_by_guild={"guild": guild_channels} if guild_channels else {},
        threads_by_guild={"guild": threads} if threads else {},
    )

    class API:
        def __init__(self):
            self.fetch_calls = []
            self.deleted_messages = []
            self.deleted_reactions = []

        def fetch_messages(self, fetched_channel_id, **_kwargs):
            self.fetch_calls.append(fetched_channel_id)
            assert fetched_channel_id == channel_id
            yield make_thread_message("mine", "me") | {"channel_id": channel_id}
            yield make_thread_message(
                "theirs",
                "other",
                reactions=[{
                    "count": 1,
                    "count_details": {"normal": 1, "burst": 0},
                    "me": True,
                    "me_burst": False,
                    "emoji": {"name": "wave"},
                }],
            ) | {"channel_id": channel_id}

        def delete_message(self, channel_id, message_id):
            self.deleted_messages.append((channel_id, message_id))
            return DeleteOutcome.DELETED

        def delete_own_reaction(
            self,
            channel_id,
            message_id,
            emoji,
            reaction_type=ReactionType.NORMAL,
        ):
            self.deleted_reactions.append(
                (channel_id, message_id, emoji, reaction_type)
            )
            return DeleteOutcome.DELETED

        def get_last_fetch_summary(self, _channel_id):
            return None

    api = API()
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=[channel_id],
        scope_inventory=inventory,
    )

    deleted = cleaner.clean_messages(
        delete_reactions=True,
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.fetch_calls == [channel_id]
    assert api.deleted_messages == [(channel_id, "mine")]
    assert api.deleted_reactions == [
        (channel_id, "theirs", {"name": "wave"}, ReactionType.NORMAL)
    ]


def archived_inventory(*, owner_id="me", locked=False, permissions=0):
    inventory = make_inventory()
    inventory.guilds[0].update({"permissions": str(permissions), "owner": False})
    for channel in inventory.guild_channels_by_guild["g1"]:
        channel["permission_overwrites"] = []
    archived = dict(inventory.threads_by_guild["g1"][1])
    archived["owner_id"] = owner_id
    archived["thread_metadata"] = {
        "archived": True,
        "locked": locked,
        "auto_archive_duration": 60,
    }
    inventory.threads_by_guild["g1"] = [archived]
    return inventory


class ArchivedCleanupAPI(ThreadCleanupAPI):
    def __init__(self, messages, *, update_outcomes=(), roles=()):
        super().__init__(messages)
        self.update_outcomes = list(update_outcomes)
        self.roles = list(roles)
        self.archive_calls = []
        self.member_calls = []

    def set_thread_archived(self, thread_id, *, archived):
        self.archive_calls.append((thread_id, archived))
        return self.update_outcomes.pop(0)

    def get_current_guild_member(self, guild_id):
        self.member_calls.append(guild_id)
        return {"roles": self.roles}


class ReArchivingCleanupAPI(ArchivedCleanupAPI):
    def __init__(
        self,
        messages,
        *,
        update_outcomes=(),
        message_outcomes=(),
        reaction_outcomes=(),
        current_channel=None,
    ):
        super().__init__(messages, update_outcomes=update_outcomes)
        self.message_outcomes = list(message_outcomes)
        self.reaction_outcomes = list(reaction_outcomes)
        self.current_channel = current_channel
        self.get_channel_calls = []

    def delete_message(self, channel_id, message_id):
        self.deleted_messages.append((channel_id, message_id))
        return self.message_outcomes.pop(0)

    def delete_own_reaction(
        self,
        channel_id,
        message_id,
        emoji,
        reaction_type=ReactionType.NORMAL,
    ):
        self.deleted_reactions.append(
            (channel_id, message_id, emoji, reaction_type)
        )
        return self.reaction_outcomes.pop(0)

    def get_channel(self, channel_id):
        self.get_channel_calls.append(channel_id)
        return self.current_channel


def archived_message(message_id, author_id, *, reactions=None):
    return make_thread_message(
        message_id,
        author_id,
        reactions=reactions,
    ) | {"channel_id": "archived-post"}


def test_archived_thread_is_skipped_without_fetching_by_default(caplog):
    caplog.set_level("INFO")
    api = ArchivedCleanupAPI([archived_message("mine", "me")])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        preserve_last=timedelta(0),
        scope_inventory=archived_inventory(),
    )

    deleted = cleaner.clean_messages(
        delete_reactions=True,
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 0
    assert api.fetch_calls == []
    assert api.deleted_messages == []
    assert api.deleted_reactions == []
    assert "1 skipped without content scan" in caplog.text


def test_excluded_archived_thread_state_stops_active_thread_race_and_continues(
    caplog,
):
    caplog.set_level("INFO")
    inventory = make_inventory()
    scope_filter = ScopeFilter.from_names(excluded_thread_states=["archived"])

    class ActiveThreadRaceAPI:
        def __init__(self):
            self.fetch_calls = []
            self.deleted_messages = []

        def fetch_messages(self, channel_id, **_kwargs):
            self.fetch_calls.append(channel_id)
            if channel_id == "active-thread":
                yield make_thread_message("thread-1", "me")
                yield make_thread_message("thread-2", "me")
            elif channel_id == "voice":
                yield make_thread_message("voice-1", "me") | {
                    "channel_id": "voice"
                }

        def delete_message(self, channel_id, message_id):
            self.deleted_messages.append((channel_id, message_id))
            if channel_id == "active-thread":
                return DeleteOutcome.THREAD_ARCHIVED
            return DeleteOutcome.DELETED

        def get_last_fetch_summary(self, _channel_id):
            return None

    api = ActiveThreadRaceAPI()
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread", "archived-post", "voice"],
        scope_inventory=inventory,
        scope_filter=scope_filter,
    )

    deleted = cleaner.clean_messages(
        archived_thread_cleanup="skip",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.fetch_calls == ["active-thread", "voice"]
    assert api.deleted_messages == [
        ("active-thread", "thread-1"),
        ("voice", "voice-1"),
    ]
    assert "Thread active-thread archived during cleanup" in caplog.text
    assert "1 interrupted by archive state changes" in caplog.text


def test_temporary_archived_cleanup_opens_once_cleans_and_restores(tmp_path):
    reactions = [{
        "count": 1,
        "count_details": {"normal": 1, "burst": 0},
        "me": True,
        "me_burst": False,
        "emoji": {"name": "wave"},
    }]
    api = ArchivedCleanupAPI(
        [
            archived_message("mine", "me"),
            archived_message("theirs", "other", reactions=reactions),
        ],
        update_outcomes=(UpdateOutcome.APPLIED, UpdateOutcome.APPLIED),
    )
    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=archived_inventory(),
        thread_restoration_journal=journal,
    )

    deleted = cleaner.clean_messages(
        archived_thread_cleanup="temporary",
        delete_reactions=True,
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.fetch_calls[0][0] == "archived-post"
    assert api.archive_calls == [
        ("archived-post", False),
        ("archived-post", True),
    ]
    assert api.deleted_messages == [("archived-post", "mine")]
    assert api.deleted_reactions == [
        (
            "archived-post",
            "theirs",
            {"name": "wave"},
            ReactionType.NORMAL,
        )
    ]
    assert journal.pending("me") == ()


def test_likely_auto_archive_reopens_retries_message_and_continues(
    tmp_path,
    caplog,
):
    caplog.set_level("INFO")
    inventory = archived_inventory()
    current_channel = inventory.threads_by_guild["g1"][0]
    api = ReArchivingCleanupAPI(
        [
            archived_message("mine-1", "me"),
            archived_message("mine-2", "me"),
        ],
        update_outcomes=(
            UpdateOutcome.APPLIED,
            UpdateOutcome.APPLIED,
            UpdateOutcome.APPLIED,
        ),
        message_outcomes=(
            DeleteOutcome.THREAD_ARCHIVED,
            DeleteOutcome.DELETED,
            DeleteOutcome.DELETED,
        ),
        current_channel=current_channel,
    )
    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=inventory,
        thread_restoration_journal=journal,
    )
    clock_values = iter((100.0, 3700.0, 3701.0))
    cleaner._thread_state_clock = clock_values.__next__

    deleted = cleaner.clean_messages(
        archived_thread_cleanup="temporary",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 2
    assert api.deleted_messages == [
        ("archived-post", "mine-1"),
        ("archived-post", "mine-1"),
        ("archived-post", "mine-2"),
    ]
    assert api.get_channel_calls == ["archived-post"]
    assert api.archive_calls == [
        ("archived-post", False),
        ("archived-post", False),
        ("archived-post", True),
    ]
    assert journal.pending("me") == ()
    assert "1 reopened after likely auto-archive" in caplog.text


def test_early_external_archive_stops_remaining_thread_actions(
    tmp_path,
    caplog,
):
    caplog.set_level("INFO")
    inventory = archived_inventory()
    api = ReArchivingCleanupAPI(
        [
            archived_message("mine-1", "me"),
            archived_message("mine-2", "me"),
        ],
        update_outcomes=(UpdateOutcome.APPLIED,),
        message_outcomes=(DeleteOutcome.THREAD_ARCHIVED,),
        current_channel=inventory.threads_by_guild["g1"][0],
    )
    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=inventory,
        thread_restoration_journal=journal,
    )
    clock_values = iter((100.0, 200.0))
    cleaner._thread_state_clock = clock_values.__next__

    deleted = cleaner.clean_messages(
        archived_thread_cleanup="temporary",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 0
    assert api.deleted_messages == [("archived-post", "mine-1")]
    assert api.archive_calls == [("archived-post", False)]
    assert journal.pending("me") == ()
    assert "1 interrupted by archive state changes" in caplog.text
    assert "1 actions not attempted" in caplog.text


def test_second_immediate_archive_does_not_loop_reactivation(tmp_path, caplog):
    caplog.set_level("INFO")
    inventory = archived_inventory()
    api = ReArchivingCleanupAPI(
        [
            archived_message("mine-1", "me"),
            archived_message("mine-2", "me"),
        ],
        update_outcomes=(
            UpdateOutcome.APPLIED,
            UpdateOutcome.APPLIED,
            UpdateOutcome.APPLIED,
        ),
        message_outcomes=(
            DeleteOutcome.THREAD_ARCHIVED,
            DeleteOutcome.THREAD_ARCHIVED,
        ),
        current_channel=inventory.threads_by_guild["g1"][0],
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=inventory,
        thread_restoration_journal=ThreadRestorationJournal(
            str(tmp_path / "journal.json")
        ),
    )
    clock_values = iter((100.0, 3700.0, 3701.0))
    cleaner._thread_state_clock = clock_values.__next__

    deleted = cleaner.clean_messages(
        archived_thread_cleanup="temporary",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 0
    assert api.deleted_messages == [
        ("archived-post", "mine-1"),
        ("archived-post", "mine-1"),
    ]
    assert api.get_channel_calls == ["archived-post"]
    assert api.archive_calls == [
        ("archived-post", False),
        ("archived-post", False),
        ("archived-post", True),
    ]
    assert "1 reopened after likely auto-archive" in caplog.text
    assert "1 interrupted by archive state changes" in caplog.text


def test_each_complete_auto_archive_interval_can_reopen_once(tmp_path, caplog):
    caplog.set_level("INFO")
    inventory = archived_inventory()
    api = ReArchivingCleanupAPI(
        [
            archived_message("mine-1", "me"),
            archived_message("mine-2", "me"),
        ],
        update_outcomes=(
            UpdateOutcome.APPLIED,
            UpdateOutcome.APPLIED,
            UpdateOutcome.APPLIED,
            UpdateOutcome.APPLIED,
        ),
        message_outcomes=(
            DeleteOutcome.THREAD_ARCHIVED,
            DeleteOutcome.DELETED,
            DeleteOutcome.THREAD_ARCHIVED,
            DeleteOutcome.DELETED,
        ),
        current_channel=inventory.threads_by_guild["g1"][0],
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=inventory,
        thread_restoration_journal=ThreadRestorationJournal(
            str(tmp_path / "journal.json")
        ),
    )
    clock_values = iter((100.0, 3700.0, 3701.0, 7301.0, 7302.0))
    cleaner._thread_state_clock = clock_values.__next__

    deleted = cleaner.clean_messages(
        archived_thread_cleanup="temporary",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 2
    assert api.get_channel_calls == ["archived-post", "archived-post"]
    assert api.archive_calls == [
        ("archived-post", False),
        ("archived-post", False),
        ("archived-post", False),
        ("archived-post", True),
    ]
    assert "2 reopened after likely auto-archive" in caplog.text


def test_likely_auto_archive_reopens_and_retries_reaction(tmp_path):
    reactions = [{
        "count": 2,
        "count_details": {"normal": 1, "burst": 1},
        "me": True,
        "me_burst": True,
        "emoji": {"name": "wave"},
    }]
    inventory = archived_inventory()
    api = ReArchivingCleanupAPI(
        [archived_message("theirs", "other", reactions=reactions)],
        update_outcomes=(
            UpdateOutcome.APPLIED,
            UpdateOutcome.APPLIED,
            UpdateOutcome.APPLIED,
        ),
        reaction_outcomes=(
            DeleteOutcome.THREAD_ARCHIVED,
            DeleteOutcome.DELETED,
            DeleteOutcome.DELETED,
        ),
        current_channel=inventory.threads_by_guild["g1"][0],
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=inventory,
        thread_restoration_journal=ThreadRestorationJournal(
            str(tmp_path / "journal.json")
        ),
    )
    clock_values = iter((100.0, 3700.0, 3701.0))
    cleaner._thread_state_clock = clock_values.__next__

    deleted = cleaner.clean_messages(
        archived_thread_cleanup="temporary",
        delete_reactions=True,
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 0
    assert len(api.deleted_reactions) == 3
    assert api.deleted_reactions[0] == api.deleted_reactions[1]
    assert api.deleted_reactions[2][-1] == ReactionType.BURST
    assert api.archive_calls == [
        ("archived-post", False),
        ("archived-post", False),
        ("archived-post", True),
    ]


def test_allow_active_can_resume_likely_auto_archive_without_journal():
    inventory = archived_inventory(owner_id="other")
    api = ReArchivingCleanupAPI(
        [archived_message("mine", "me")],
        update_outcomes=(
            UpdateOutcome.APPLIED,
            UpdateOutcome.APPLIED,
            UpdateOutcome.APPLIED,
        ),
        message_outcomes=(
            DeleteOutcome.THREAD_ARCHIVED,
            DeleteOutcome.DELETED,
        ),
        current_channel=inventory.threads_by_guild["g1"][0],
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=inventory,
    )
    clock_values = iter((100.0, 3700.0, 3701.0))
    cleaner._thread_state_clock = clock_values.__next__

    deleted = cleaner.clean_messages(
        archived_thread_cleanup="allow-active",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.member_calls == ["g1"]
    assert api.archive_calls == [
        ("archived-post", False),
        ("archived-post", False),
        ("archived-post", True),
    ]


def test_archived_thread_absent_during_restoration_is_reported_separately(
    tmp_path,
    caplog,
):
    caplog.set_level("INFO")
    api = ArchivedCleanupAPI(
        [archived_message("mine", "me")],
        update_outcomes=(UpdateOutcome.APPLIED, UpdateOutcome.ABSENT),
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=archived_inventory(),
        thread_restoration_journal=ThreadRestorationJournal(
            str(tmp_path / "journal.json")
        ),
    )

    deleted = cleaner.clean_messages(
        archived_thread_cleanup="temporary",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert "1 absent during restoration" in caplog.text
    assert "1 restored" not in caplog.text


def test_temporary_archived_cleanup_dry_run_never_changes_thread_state(
    caplog,
):
    caplog.set_level(PROGRESS_LEVEL)
    api = ArchivedCleanupAPI([archived_message("mine", "me")])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=archived_inventory(),
    )

    deleted = cleaner.clean_messages(
        dry_run=True,
        archived_thread_cleanup="temporary",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.archive_calls == []
    assert api.deleted_messages == []
    assert "Would attempt to unarchive thread" in caplog.text
    assert "would restore it to archived state" in caplog.text


def test_empty_archived_cleanup_plan_does_not_unarchive(tmp_path):
    api = ArchivedCleanupAPI([archived_message("theirs", "other")])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=archived_inventory(),
        thread_restoration_journal=ThreadRestorationJournal(
            str(tmp_path / "journal.json")
        ),
    )

    deleted = cleaner.clean_messages(
        archived_thread_cleanup="temporary",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 0
    assert len(api.fetch_calls) == 1
    assert api.archive_calls == []


def test_temporary_mode_skips_noncreator_without_manage_threads():
    api = ArchivedCleanupAPI([archived_message("mine", "me")])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=archived_inventory(owner_id="other"),
    )

    deleted = cleaner.clean_messages(
        archived_thread_cleanup="temporary",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 0
    assert api.member_calls == ["g1"]
    assert api.fetch_calls == []
    assert api.archive_calls == []


def test_allow_active_cleans_noncreator_thread_when_restore_fails(caplog):
    caplog.set_level("WARNING")
    api = ArchivedCleanupAPI(
        [archived_message("mine", "me")],
        update_outcomes=(UpdateOutcome.APPLIED, UpdateOutcome.FAILED),
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=archived_inventory(owner_id="other"),
    )

    deleted = cleaner.clean_messages(
        archived_thread_cleanup="allow-active",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.archive_calls == [
        ("archived-post", False),
        ("archived-post", True),
    ]
    assert "remains active after cleanup" in caplog.text


def test_locked_creator_thread_is_not_scanned_without_manage_threads():
    api = ArchivedCleanupAPI([archived_message("mine", "me")])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=archived_inventory(locked=True),
    )

    cleaner.clean_messages(
        archived_thread_cleanup="allow-active",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert api.fetch_calls == []
    assert api.archive_calls == []


def test_locked_thread_manager_cleans_without_changing_locked_state(tmp_path):
    api = ArchivedCleanupAPI(
        [archived_message("mine", "me")],
        update_outcomes=(UpdateOutcome.APPLIED, UpdateOutcome.APPLIED),
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=archived_inventory(
            owner_id="other",
            locked=True,
            permissions=MANAGE_THREADS_PERMISSION,
        ),
        thread_restoration_journal=ThreadRestorationJournal(
            str(tmp_path / "journal.json")
        ),
    )

    deleted = cleaner.clean_messages(
        archived_thread_cleanup="temporary",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.archive_calls == [
        ("archived-post", False),
        ("archived-post", True),
    ]
    assert api.deleted_messages == [("archived-post", "mine")]


def test_temporary_cleanup_restores_thread_when_message_deletion_raises(
    tmp_path,
):
    class ExplodingAPI(ArchivedCleanupAPI):
        def delete_message(self, channel_id, message_id):
            raise RuntimeError("simulated cleanup failure")

    journal = ThreadRestorationJournal(str(tmp_path / "journal.json"))
    api = ExplodingAPI(
        [archived_message("mine", "me")],
        update_outcomes=(UpdateOutcome.APPLIED, UpdateOutcome.APPLIED),
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=archived_inventory(),
        thread_restoration_journal=journal,
    )

    with pytest.raises(RuntimeError, match="simulated cleanup failure"):
        cleaner.clean_messages(
            archived_thread_cleanup="temporary",
            fetch_sleep_time_range=(0, 0),
            delete_sleep_time_range=(0, 0),
        )

    assert api.archive_calls == [
        ("archived-post", False),
        ("archived-post", True),
    ]
    assert journal.pending("me") == ()


def test_failed_unarchive_does_not_execute_planned_actions(tmp_path):
    api = ArchivedCleanupAPI(
        [archived_message("mine", "me")],
        update_outcomes=(UpdateOutcome.FAILED,),
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["archived-post"],
        scope_inventory=archived_inventory(),
        thread_restoration_journal=ThreadRestorationJournal(
            str(tmp_path / "journal.json")
        ),
    )

    deleted = cleaner.clean_messages(
        archived_thread_cleanup="temporary",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 0
    assert api.archive_calls == [("archived-post", False)]
    assert api.deleted_messages == []


def test_owned_thread_deletion_is_disabled_by_default():
    api = ThreadCleanupAPI([make_thread_message("mine", "me")])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(),
    )

    deleted = cleaner.clean_messages(
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_threads == []
    assert api.deleted_messages == [("active-thread", "mine")]


def test_owned_thread_all_mode_deletes_without_scanning_messages():
    api = ThreadCleanupAPI([make_thread_message("theirs", "other")])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(),
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="all",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 0
    assert api.deleted_threads == ["active-thread"]
    assert api.fetch_calls == []


def test_owned_forum_post_uses_the_same_opt_in_thread_delete_path():
    inventory = make_inventory()
    forum_post = dict(inventory.threads_by_guild["g1"][1])
    forum_post.update({
        "id": "owned-forum-post",
        "owner_id": "me",
        "message_count": 1,
    })
    inventory.threads_by_guild["g1"] = [forum_post]
    api = ThreadCleanupAPI([])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["forum"],
        scope_inventory=inventory,
    )

    cleaner.clean_messages(
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
        delete_owned_threads="all",
    )

    assert api.deleted_threads == ["owned-forum-post"]
    assert api.fetch_calls == []
    assert api.deleted_messages == []


def test_owned_thread_all_mode_skips_thread_created_by_another_user():
    api = ThreadCleanupAPI([make_thread_message("mine", "me")])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(owner_id="other"),
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="all",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_threads == []
    assert api.deleted_messages == [("active-thread", "mine")]


def test_owned_thread_all_mode_falls_back_when_owner_is_missing():
    api = ThreadCleanupAPI([make_thread_message("mine", "me")])
    inventory = make_owned_thread_inventory()
    inventory.threads_by_guild["g1"][0].pop("owner_id")
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=inventory,
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="all",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_threads == []
    assert api.deleted_messages == [("active-thread", "mine")]


def test_owned_thread_all_mode_dry_run_only_plans_deletion(caplog):
    caplog.set_level(PROGRESS_LEVEL)
    api = ThreadCleanupAPI(
        [
            make_thread_message(
                "mine",
                "me",
                reactions=[
                    {
                        "count": 4,
                        "count_details": {"normal": 3, "burst": 1},
                        "me": True,
                        "me_burst": False,
                        "emoji": {"name": "one"},
                    }
                ],
            ),
            make_thread_message(
                "theirs",
                "other",
                reactions=[
                    {
                        "count": 3,
                        "count_details": {"normal": 1, "burst": 2},
                        "me": False,
                        "me_burst": True,
                        "emoji": {"name": "two"},
                    }
                ],
            ),
        ]
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(message_count=2),
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="all",
        dry_run=True,
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 0
    assert api.deleted_threads == []
    assert len(api.fetch_calls) == 1
    assert (
        "Impact at scan time for PublicThread Active thread (ID: active-thread): "
        "messages 1 yours / 1 other-or-unknown; foreign reactions 3 normal / 2 super."
        in caplog.text
    )
    assert "owned threads 1 delete" in caplog.text
    assert "foreign messages affected 1" in caplog.text
    assert "foreign reactions affected 3 normal / 2 super" in caplog.text


def test_owned_thread_all_mode_falls_back_when_discord_rejects_deletion():
    api = ThreadCleanupAPI(
        [make_thread_message("mine", "me")],
        delete_thread_result=DeleteOutcome.FAILED,
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(),
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="all",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_threads == ["active-thread"]
    assert len(api.fetch_calls) == 1
    assert api.deleted_messages == [("active-thread", "mine")]


def test_owned_thread_absent_is_terminal_without_claiming_deletion(caplog):
    api = ThreadCleanupAPI(
        [make_thread_message("mine", "me")],
        delete_thread_result=DeleteOutcome.ABSENT,
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(),
    )

    with caplog.at_level("INFO"):
        deleted = cleaner.clean_messages(
            delete_owned_threads="all",
            fetch_sleep_time_range=(0, 0),
            delete_sleep_time_range=(0, 0),
        )

    assert deleted == 0
    assert api.deleted_threads == ["active-thread"]
    assert api.deleted_messages == []
    assert "owned threads 0 deleted / 1 absent / 0 failed" in caplog.text


def test_owned_thread_self_only_deletes_after_complete_all_own_scan():
    api = ThreadCleanupAPI(
        [
            make_thread_message(
                "mine",
                "me",
                reactions=[{"me": False, "emoji": {"name": "wave"}}],
            )
        ]
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(),
    )
    fetch_since = datetime(2025, 1, 1, tzinfo=timezone.utc)

    deleted = cleaner.clean_messages(
        delete_owned_threads="self-only",
        fetch_since=fetch_since,
        max_messages=1,
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 0
    assert api.deleted_threads == ["active-thread"]
    assert len(api.fetch_calls) == 1
    _, scan_options = api.fetch_calls[0]
    assert scan_options["fetch_since"] is None
    assert scan_options["max_messages"] == float("inf")


def test_owned_thread_self_only_falls_back_for_foreign_message():
    api = ThreadCleanupAPI(
        [
            make_thread_message("mine", "me"),
            make_thread_message("theirs", "other"),
        ]
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(message_count=2),
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="self-only",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_threads == []
    assert len(api.fetch_calls) == 1
    assert api.deleted_messages == [("active-thread", "mine")]


def test_owned_thread_self_only_falls_back_when_scan_is_not_proven_complete(
    caplog,
):
    caplog.set_level(PROGRESS_LEVEL)
    api = ThreadCleanupAPI(
        [
            make_thread_message(
                "mine",
                "me",
                reactions=[
                    {
                        "count": 2,
                        "count_details": {"normal": 2, "burst": 0},
                        "me": True,
                        "me_burst": False,
                        "emoji": {"name": "wave"},
                    }
                ],
            )
        ],
        complete=False,
    )
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(),
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="self-only",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_threads == []
    assert api.deleted_messages == [("active-thread", "mine")]
    assert (
        "messages unknown (incomplete thread scan); foreign reactions unknown"
        in caplog.text
    )


def test_owned_thread_self_only_falls_back_when_thread_count_is_inconsistent():
    api = ThreadCleanupAPI([make_thread_message("mine", "me")])
    cleaner = MessageCleaner(
        api=api,
        user_id="me",
        include_ids=["active-thread"],
        scope_inventory=make_owned_thread_inventory(message_count=2),
    )

    deleted = cleaner.clean_messages(
        delete_owned_threads="self-only",
        fetch_sleep_time_range=(0, 0),
        delete_sleep_time_range=(0, 0),
    )

    assert deleted == 1
    assert api.deleted_threads == []
    assert api.deleted_messages == [("active-thread", "mine")]
