from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.rate_limits import (
    THREAD_SEARCH_POLICY,
    DiscordRequestScheduler,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResponse:
    def __init__(self, status_code: int, payload=None, headers=None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


def make_scheduler(
    clock: FakeClock,
    *,
    retry_jitter: tuple[float, float] = (0.0, 0.0),
) -> DiscordRequestScheduler:
    return DiscordRequestScheduler(
        retry_jitter=retry_jitter,
        default_interval=(0.0, 0.0),
        clock=clock,
        wall_clock=clock,
        sleeper=clock.sleep,
        random_between=lambda minimum, _maximum: minimum,
    )


def test_route_key_normalizes_ids_and_preserves_major_parameter():
    first = DiscordRequestScheduler.route_key(
        "get",
        "https://discord.com/api/v10/channels/123456789012345678/messages/223456789012345678",
    )
    same_route = DiscordRequestScheduler.route_key(
        "GET",
        "https://discord.com/api/v10/channels/123456789012345678/messages/323456789012345678",
    )
    other_channel = DiscordRequestScheduler.route_key(
        "GET",
        "https://discord.com/api/v10/channels/423456789012345678/messages/323456789012345678",
    )

    assert first == same_route
    assert first.template == "/channels/{id}/messages/{id}"
    assert first.major_parameter == "channels:123456789012345678"
    assert first != other_channel


def test_application_pacing_waits_before_the_next_request_only():
    clock = FakeClock()
    scheduler = make_scheduler(clock)
    scheduler.configure_policy("delete", (1.5, 1.5))

    first = scheduler.acquire(
        "DELETE", "https://discord.com/api/v10/channels/1/messages/1", policy="delete"
    )
    scheduler.observe(first, FakeResponse(204))

    assert clock.sleeps == []

    second = scheduler.acquire(
        "DELETE", "https://discord.com/api/v10/channels/2/messages/2", policy="delete"
    )

    assert clock.sleeps == [1.5]
    assert second.wait_reasons == ("policy",)
    assert scheduler.wait_snapshot("delete").count == 1
    assert scheduler.wait_snapshot("delete").seconds == pytest.approx(1.5)


def test_thread_search_policy_has_a_default_floor():
    clock = FakeClock()
    scheduler = make_scheduler(clock)
    first_url = (
        "https://discord.com/api/v10/channels/123456789012345678/"
        "threads/search"
    )
    second_url = (
        "https://discord.com/api/v10/channels/223456789012345678/"
        "threads/search"
    )
    first = scheduler.acquire(
        "GET",
        first_url,
        policy=THREAD_SEARCH_POLICY,
    )
    scheduler.observe(first, FakeResponse(200, [{"threads": []}]))

    second = scheduler.acquire(
        "GET",
        second_url,
        policy=THREAD_SEARCH_POLICY,
    )

    assert clock.sleeps == pytest.approx([0.2])
    assert second.wait_reasons == ("policy",)


def test_policy_interval_can_be_overridden():
    clock = FakeClock()
    scheduler = DiscordRequestScheduler(
        retry_jitter=(0, 0),
        default_interval=(0, 0),
        policy_intervals={THREAD_SEARCH_POLICY: (2.0, 2.0)},
        clock=clock,
        wall_clock=clock,
        sleeper=clock.sleep,
        random_between=lambda minimum, _maximum: minimum,
    )
    first = scheduler.acquire(
        "GET",
        "https://discord.com/api/v10/channels/123456789012345678/threads",
        policy=THREAD_SEARCH_POLICY,
    )
    scheduler.observe(first, FakeResponse(200, []))

    scheduler.acquire(
        "GET",
        "https://discord.com/api/v10/channels/223456789012345678/threads",
        policy=THREAD_SEARCH_POLICY,
    )

    assert clock.sleeps == [2.0]


def test_exhausted_bucket_blocks_only_matching_major_resource():
    clock = FakeClock()
    scheduler = make_scheduler(clock)
    channel_one = "https://discord.com/api/v10/channels/123456789012345678/messages"
    channel_two = "https://discord.com/api/v10/channels/223456789012345678/messages"

    first = scheduler.acquire("GET", channel_one)
    scheduler.observe(
        first,
        FakeResponse(
            200,
            [],
            {
                "X-RateLimit-Bucket": "messages",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset-After": "2.25",
            },
        ),
    )

    scheduler.acquire("GET", channel_two)
    assert clock.sleeps == []

    matching = scheduler.acquire("GET", channel_one)
    assert clock.sleeps == [2.25]
    assert matching.wait_reasons == ("bucket",)


def test_absolute_bucket_reset_is_used_when_reset_after_is_missing():
    clock = FakeClock()
    scheduler = make_scheduler(clock)
    url = "https://discord.com/api/v10/channels/123456789012345678/messages"
    first = scheduler.acquire("GET", url)
    scheduler.observe(
        first,
        FakeResponse(
            200,
            [],
            {
                "X-RateLimit-Bucket": "messages",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "103.5",
            },
        ),
    )

    scheduler.acquire("GET", url)

    assert clock.sleeps == [3.5]


def test_routes_with_same_bucket_and_major_resource_share_window():
    clock = FakeClock()
    scheduler = make_scheduler(clock)
    channel_id = "123456789012345678"
    collection_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    item_url = f"{collection_url}/223456789012345678"

    collection = scheduler.acquire("GET", collection_url)
    scheduler.observe(
        collection,
        FakeResponse(
            200, [], {"X-RateLimit-Bucket": "shared", "X-RateLimit-Remaining": "1"}
        ),
    )
    item = scheduler.acquire("DELETE", item_url)
    scheduler.observe(
        item,
        FakeResponse(
            204,
            headers={
                "X-RateLimit-Bucket": "shared",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset-After": "3",
            },
        ),
    )

    blocked = scheduler.acquire("GET", collection_url)

    assert clock.sleeps == [3.0]
    assert blocked.wait_reasons == ("bucket",)


def test_global_429_blocks_an_unrelated_route():
    clock = FakeClock()
    scheduler = make_scheduler(clock)
    first = scheduler.acquire("GET", "https://discord.com/api/v10/users/@me/guilds")
    outcome = scheduler.observe(
        first,
        FakeResponse(429, {"retry_after": 4.0, "global": True}),
    )

    unrelated = scheduler.acquire(
        "GET",
        "https://discord.com/api/v10/channels/123456789012345678/messages",
    )

    assert outcome.scope == "global"
    assert clock.sleeps == [4.0]
    assert unrelated.wait_reasons == ("global",)


def test_429_uses_absolute_reset_when_retry_after_is_missing():
    clock = FakeClock()
    scheduler = make_scheduler(clock)
    url = "https://discord.com/api/v10/users/@me/guilds"
    first = scheduler.acquire("GET", url)
    outcome = scheduler.observe(
        first,
        FakeResponse(429, {}, {"X-RateLimit-Reset": "102.75"}),
    )

    scheduler.acquire("GET", url)

    assert outcome.retry_after == pytest.approx(2.75)
    assert clock.sleeps == [2.75]


def test_user_429_learns_route_family_interval_across_channel_ids():
    clock = FakeClock()
    scheduler = make_scheduler(clock)
    first_url = "https://discord.com/api/v10/channels/123456789012345678/messages"
    second_url = "https://discord.com/api/v10/channels/223456789012345678/messages"
    third_url = "https://discord.com/api/v10/channels/323456789012345678/messages"

    first = scheduler.acquire("GET", first_url)
    scheduler.observe(first, FakeResponse(200, []))
    clock.now += 0.2

    limited = scheduler.acquire("GET", second_url)
    outcome = scheduler.observe(
        limited,
        FakeResponse(
            429,
            {"retry_after": 0.8, "global": False},
            {"X-RateLimit-Scope": "user"},
        ),
    )
    retry = scheduler.acquire("GET", second_url)
    scheduler.observe(retry, FakeResponse(200, []))

    unrelated = scheduler.acquire("GET", "https://discord.com/api/v10/users/@me")
    third = scheduler.acquire("GET", third_url)

    assert outcome.server_retry_after == pytest.approx(0.8)
    assert outcome.learned_family_interval == pytest.approx(1.0)
    assert retry.waited_seconds == pytest.approx(0.8)
    assert unrelated.waited_seconds == 0
    assert third.waited_seconds == pytest.approx(1.0)
    assert third.wait_reasons == ("route-family",)


def test_429_without_bucket_uses_route_fallback():
    clock = FakeClock()
    scheduler = make_scheduler(clock)
    guilds_url = "https://discord.com/api/v10/users/@me/guilds"
    first = scheduler.acquire("GET", guilds_url)
    scheduler.observe(first, FakeResponse(429, {"retry_after": 2.0, "global": False}))

    scheduler.acquire("GET", "https://discord.com/api/v10/users/@me/channels")
    assert clock.sleeps == []

    matching = scheduler.acquire("GET", guilds_url)
    assert clock.sleeps == [2.0]
    assert matching.wait_reasons == ("route",)


def test_429_without_bucket_reuses_a_previously_learned_bucket():
    clock = FakeClock()
    scheduler = make_scheduler(clock)
    url = "https://discord.com/api/v10/channels/123456789012345678/messages"
    first = scheduler.acquire("GET", url)
    scheduler.observe(
        first,
        FakeResponse(
            200, [], {"x-ratelimit-bucket": "messages", "x-ratelimit-remaining": "1"}
        ),
    )
    limited = scheduler.acquire("GET", url)
    outcome = scheduler.observe(limited, FakeResponse(429, {"retry_after": 1.75}))

    matching = scheduler.acquire("GET", url)

    assert outcome.bucket_id == "messages"
    assert clock.sleeps == [1.75]
    assert matching.wait_reasons == ("bucket",)


def test_longest_constraint_is_waited_once():
    clock = FakeClock()
    scheduler = make_scheduler(clock)
    scheduler.configure_policy("fetch", (1.0, 1.0))
    url = "https://discord.com/api/v10/channels/123456789012345678/messages"
    first = scheduler.acquire("GET", url, policy="fetch")
    scheduler.observe(
        first,
        FakeResponse(
            200,
            [],
            {
                "X-RateLimit-Bucket": "messages",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset-After": "3",
            },
        ),
    )

    scheduler.acquire("GET", url, policy="fetch")

    assert clock.sleeps == [3.0]


def test_retry_delay_prefers_response_value_and_adds_configured_jitter():
    clock = FakeClock()
    scheduler = make_scheduler(clock, retry_jitter=(0.25, 0.5))

    assert scheduler.retry_delay_for_response(
        FakeResponse(503, {}, {"Retry-After": "2.5"}),
        fallback=1.0,
    ) == pytest.approx(2.75)
    assert scheduler.retry_delay_for_response(
        FakeResponse(503, {"retry_after": "invalid"}),
        fallback=1.0,
    ) == pytest.approx(1.25)
    assert scheduler.retry_delay_for_response(
        FakeResponse(503, ValueError("not json"), {"Retry-After": "2"}),
        fallback=1.0,
    ) == pytest.approx(2.25)


@pytest.mark.parametrize(
    "interval",
    [(-1.0, 0.0), (2.0, 1.0), (0.0, float("inf"))],
)
def test_invalid_intervals_are_rejected(interval):
    clock = FakeClock()
    scheduler = make_scheduler(clock)

    with pytest.raises(ValueError, match="finite, non-negative, and ordered"):
        scheduler.configure_policy("fetch", interval)


def test_interval_requires_two_values():
    clock = FakeClock()
    scheduler = make_scheduler(clock)

    with pytest.raises(ValueError, match="exactly two values"):
        scheduler.configure_policy("fetch", (1.0,))


def test_invalid_retry_delay_is_rejected():
    clock = FakeClock()
    scheduler = make_scheduler(clock)

    with pytest.raises(ValueError, match="finite and non-negative"):
        scheduler.retry_delay(float("nan"))


def test_unknown_policy_override_is_rejected():
    clock = FakeClock()
    scheduler = make_scheduler(clock)

    with pytest.raises(ValueError, match="Unknown request policy"):
        scheduler.configure_policy("typo", (1.0, 1.0))
