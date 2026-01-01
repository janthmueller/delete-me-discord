import pytest

import sys
from datetime import timedelta
from pathlib import Path

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.cleaner import MessageCleaner


class DummyApi:
    def __init__(self, messages_by_id):
        self.messages_by_id = messages_by_id

    def fetch_message_by_id(self, channel_id: str, message_id: str):
        # channel_id is ignored in this stub
        return self.messages_by_id.get(message_id)


def make_cleaner(messages_by_id=None):
    api = DummyApi(messages_by_id or {})
    return MessageCleaner(
        api=api,
        user_id="user",
        preserve_last=timedelta(weeks=2),
        preserve_n=0,
    )


def gen_main(ids):
    for mid in ids:
        yield {"message_id": mid}


def test_merge_cached_messages_emits_cached_then_main_in_order():
    # cached newer IDs should be emitted before the main stream entry
    cached_ids = ["95", "85"]
    main_ids = ["90", "80", "70"]
    messages_by_id = {mid: {"message_id": mid} for mid in cached_ids}
    cleaner = make_cleaner(messages_by_id)

    merged = list(
        cleaner._merge_cached_messages(
            channel={"id": "c1"},
            main_messages=gen_main(main_ids),
            cached_ids=cached_ids,
        )
    )

    assert [m["message_id"] for m in merged] == ["95", "90", "85", "80", "70"]


def test_merge_cached_messages_prefers_main_over_cache_duplicates():
    # If the same ID appears in cache and main, main should win and cache skipped.
    cached_ids = ["90", "70"]
    main_ids = ["90", "80"]
    messages_by_id = {mid: {"message_id": mid} for mid in cached_ids}
    cleaner = make_cleaner(messages_by_id)

    merged = list(
        cleaner._merge_cached_messages(
            channel={"id": "c1"},
            main_messages=gen_main(main_ids),
            cached_ids=cached_ids,
        )
    )

    assert [m["message_id"] for m in merged] == ["90", "80", "70"]


def test_merge_cached_messages_emits_remaining_cache_after_main():
    cached_ids = ["85", "75", "65"]
    main_ids = ["90", "80"]
    messages_by_id = {mid: {"message_id": mid} for mid in cached_ids}
    cleaner = make_cleaner(messages_by_id)

    merged = list(
        cleaner._merge_cached_messages(
            channel={"id": "c1"},
            main_messages=gen_main(main_ids),
            cached_ids=cached_ids,
        )
    )

    assert [m["message_id"] for m in merged] == ["90", "85", "80", "75", "65"]


def test_merge_cached_messages_raises_on_ascending_cache_ids():
    cleaner = make_cleaner({})
    with pytest.raises(ValueError):
        list(
            cleaner._merge_cached_messages(
                channel={"id": "c1"},
                main_messages=gen_main(["3", "2", "1"]),
                cached_ids=["10", "20"],  # ascending should trigger error
            )
        )
