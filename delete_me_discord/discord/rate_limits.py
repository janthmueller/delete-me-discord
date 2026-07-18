"""Discord request scheduling and learned rate-limit state."""

from __future__ import annotations

import math
import random
import re
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


Interval = tuple[float, float]

READ_POLICY = "read"
FETCH_POLICY = "fetch"
DELETE_POLICY = "delete"
THREAD_SEARCH_POLICY = "thread-search"

REQUEST_POLICY_DEFAULTS: dict[str, Interval] = {
    READ_POLICY: (0.1, 0.25),
    FETCH_POLICY: (0.2, 0.4),
    DELETE_POLICY: (1.5, 2.0),
    THREAD_SEARCH_POLICY: (0.2, 0.4),
}


@dataclass(frozen=True)
class RouteKey:
    method: str
    template: str
    major_parameter: str | None

    @property
    def family(self) -> "RouteFamilyKey":
        return RouteFamilyKey(method=self.method, template=self.template)


@dataclass(frozen=True)
class RouteFamilyKey:
    method: str
    template: str


@dataclass(frozen=True)
class BucketKey:
    bucket_id: str
    major_parameter: str | None


@dataclass
class RateLimitWindow:
    last_request_at: float | None = None
    next_allowed_at: float = 0.0
    limit: int | None = None
    remaining: int | None = None


@dataclass
class RouteFamilyWindow(RateLimitWindow):
    learned_interval: float = 0.0


@dataclass(frozen=True)
class RequestContext:
    route: RouteKey
    family: RouteFamilyKey
    policy: str
    started_at: float
    waited_seconds: float
    wait_reasons: tuple[str, ...]


@dataclass(frozen=True)
class RateLimitOutcome:
    limited: bool = False
    retry_after: float = 0.0
    server_retry_after: float = 0.0
    safety_jitter: float = 0.0
    scope: str | None = None
    bucket_id: str | None = None
    learned_family_interval: float = 0.0
    used_fallback: bool = False


@dataclass(frozen=True)
class WaitSnapshot:
    count: int
    seconds: float


class DiscordRequestScheduler:
    """Coordinate Discord buckets and application-level request pacing."""

    _SNOWFLAKE = re.compile(r"^\d{6,}$")
    _API_VERSION = re.compile(r"^v\d+$")

    def __init__(
        self,
        *,
        retry_jitter: Interval = (0.1, 0.3),
        default_interval: Interval = (0.1, 0.25),
        policy_intervals: Mapping[str, Interval] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        random_between: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleeper
        self._random_between = random_between
        self._retry_jitter = self._validated_interval(retry_jitter)
        self._policies = {
            name: self._validated_interval(interval)
            for name, interval in REQUEST_POLICY_DEFAULTS.items()
        }
        self._policies[READ_POLICY] = self._validated_interval(default_interval)
        for name, interval in (policy_intervals or {}).items():
            self.configure_policy(name, interval)
        self._global_until = 0.0
        self._route_to_bucket: dict[RouteKey, BucketKey] = {}
        self._route_windows: dict[RouteKey, RateLimitWindow] = {}
        self._family_windows: dict[RouteFamilyKey, RouteFamilyWindow] = {}
        self._bucket_windows: dict[BucketKey, RateLimitWindow] = {}
        self._policy_windows: dict[str, RateLimitWindow] = {}
        self._wait_counts: dict[str, int] = {}
        self._wait_seconds: dict[str, float] = {}

    def configure_policy(self, name: str, interval: Interval) -> None:
        if name not in REQUEST_POLICY_DEFAULTS:
            expected = ", ".join(sorted(REQUEST_POLICY_DEFAULTS))
            raise ValueError(
                f"Unknown request policy '{name}'. Expected one of: {expected}."
            )
        self._policies[name] = self._validated_interval(interval)

    def wait_snapshot(self, policy: str) -> WaitSnapshot:
        return WaitSnapshot(
            count=self._wait_counts.get(policy, 0),
            seconds=self._wait_seconds.get(policy, 0.0),
        )

    def retry_delay(self, base_delay: float) -> float:
        """Add configured safety jitter to a server-provided minimum delay."""
        parsed_delay = self._optional_non_negative_float(base_delay)
        if parsed_delay is None:
            raise ValueError("Retry delays must be finite and non-negative.")
        return parsed_delay + self._sample(self._retry_jitter)

    def fallback_retry_delay(self, maximum_delay: float) -> float:
        """Sample full jitter between zero and an exponential backoff cap."""
        parsed_delay = self._optional_non_negative_float(maximum_delay)
        if parsed_delay is None:
            raise ValueError("Retry delays must be finite and non-negative.")
        return self._random_between(0.0, parsed_delay)

    def retry_delay_for_response(self, response: Any, *, fallback: float) -> float:
        headers = getattr(response, "headers", {}) or {}
        payload = self._json_object(response)
        retry_after = self._retry_hint(headers, payload)
        if retry_after is None:
            return self.fallback_retry_delay(fallback)
        return self.retry_delay(retry_after)

    def acquire(
        self, method: str, url: str, *, policy: str = READ_POLICY
    ) -> RequestContext:
        if policy not in self._policies:
            expected = ", ".join(sorted(self._policies))
            raise ValueError(f"Unknown request policy '{policy}'. Expected one of: {expected}.")
        route = self.route_key(method, url)
        now = self._clock()
        constraints: list[tuple[str, float]] = [("global", self._global_until)]

        route_window = self._route_windows.get(route)
        if route_window is not None:
            constraints.append(("route", route_window.next_allowed_at))

        family_window = self._family_windows.get(route.family)
        if family_window is not None:
            constraints.append(("route-family", family_window.next_allowed_at))

        bucket_key = self._route_to_bucket.get(route)
        if bucket_key is not None:
            bucket_window = self._bucket_windows.get(bucket_key)
            if bucket_window is not None:
                constraints.append(("bucket", bucket_window.next_allowed_at))

        policy_window = self._policy_windows.get(policy)
        if policy_window is not None:
            constraints.append(("policy", policy_window.next_allowed_at))

        ready_at = max(until for _, until in constraints)
        waited_seconds = max(0.0, ready_at - now)
        wait_reasons = tuple(
            reason
            for reason, until in constraints
            if waited_seconds > 0
            and math.isclose(until, ready_at, rel_tol=0.0, abs_tol=1e-9)
        )
        if waited_seconds > 0:
            self._sleep(waited_seconds)
            self._wait_counts[policy] = self._wait_counts.get(policy, 0) + 1
            self._wait_seconds[policy] = (
                self._wait_seconds.get(policy, 0.0) + waited_seconds
            )

        started_at = max(ready_at, self._clock())
        return RequestContext(
            route=route,
            family=route.family,
            policy=policy,
            started_at=started_at,
            waited_seconds=waited_seconds,
            wait_reasons=wait_reasons,
        )

    def observe(
        self,
        context: RequestContext,
        response: Any,
        *,
        retry_fallback: float = 1.0,
    ) -> RateLimitOutcome:
        now = self._clock()
        self._record_application_pacing(context)
        previous_family_request_at = self._record_family_pacing(context)
        route_window = self._route_windows.setdefault(context.route, RateLimitWindow())
        route_window.last_request_at = context.started_at
        headers = getattr(response, "headers", {}) or {}
        bucket_id = self._header(headers, "X-RateLimit-Bucket")
        bucket_key = self._route_to_bucket.get(context.route)
        if bucket_id:
            bucket_key = BucketKey(str(bucket_id), context.route.major_parameter)
            self._route_to_bucket[context.route] = bucket_key

        window = (
            self._bucket_windows.setdefault(bucket_key, RateLimitWindow())
            if bucket_key
            else None
        )
        if window is not None:
            window.last_request_at = context.started_at
            window.limit = self._optional_int(
                self._header(headers, "X-RateLimit-Limit")
            )
            window.remaining = self._optional_int(
                self._header(headers, "X-RateLimit-Remaining")
            )

        if getattr(response, "status_code", None) == 429:
            payload = self._json_object(response)
            retry_hint = self._retry_hint(headers, payload)
            used_fallback = retry_hint is None
            if used_fallback:
                server_retry_after = 0.0
                retry_after = self.fallback_retry_delay(retry_fallback)
                safety_jitter = 0.0
            else:
                server_retry_after = retry_hint
                retry_after = self.retry_delay(server_retry_after)
                safety_jitter = retry_after - server_retry_after
            scope = self._rate_limit_scope(headers, payload)
            blocked_until = now + retry_after
            if scope == "global":
                self._global_until = max(self._global_until, blocked_until)
            elif window is not None:
                window.next_allowed_at = max(window.next_allowed_at, blocked_until)
            else:
                route_window.next_allowed_at = max(
                    route_window.next_allowed_at, blocked_until
                )
            learned_family_interval = 0.0
            if scope == "user":
                family_window = self._family_windows.setdefault(
                    context.family,
                    RouteFamilyWindow(),
                )
                if not used_fallback:
                    learned_family_interval = self._learn_family_interval(
                        context=context,
                        previous_request_at=previous_family_request_at,
                        server_retry_after=server_retry_after,
                    )
                family_window.next_allowed_at = max(
                    family_window.next_allowed_at,
                    blocked_until,
                )
            return RateLimitOutcome(
                limited=True,
                retry_after=retry_after,
                server_retry_after=server_retry_after,
                safety_jitter=safety_jitter,
                scope=scope,
                bucket_id=bucket_key.bucket_id if bucket_key else None,
                learned_family_interval=learned_family_interval,
                used_fallback=used_fallback,
            )

        reset_after = self._reset_after(headers)
        if window is not None and window.remaining == 0 and reset_after is not None:
            blocked_until = now + reset_after + self._sample(self._retry_jitter)
            window.next_allowed_at = max(window.next_allowed_at, blocked_until)

        return RateLimitOutcome(bucket_id=bucket_key.bucket_id if bucket_key else None)

    def defer_route(
        self, context: RequestContext, delay: float, *, record_attempt: bool = False
    ) -> None:
        if record_attempt:
            self._record_application_pacing(context)
            self._record_family_pacing(context)
        route_window = self._route_windows.setdefault(context.route, RateLimitWindow())
        route_window.last_request_at = context.started_at
        route_window.next_allowed_at = max(
            route_window.next_allowed_at,
            self._clock() + max(0.0, delay),
        )

    @classmethod
    def route_key(cls, method: str, url: str) -> RouteKey:
        segments = [segment for segment in urlsplit(url).path.split("/") if segment]
        if "api" in segments:
            api_index = segments.index("api")
            segments = segments[api_index + 1 :]
        if segments and cls._API_VERSION.match(segments[0]):
            segments = segments[1:]

        major_parameter = None
        for marker in ("channels", "guilds", "webhooks"):
            try:
                marker_index = segments.index(marker)
            except ValueError:
                continue
            if marker_index + 1 < len(segments):
                major_parameter = f"{marker}:{segments[marker_index + 1]}"
                break

        normalized: list[str] = []
        for index, segment in enumerate(segments):
            previous = segments[index - 1] if index else None
            if cls._SNOWFLAKE.match(segment):
                normalized.append("{id}")
            elif previous == "reactions" and segment != "@me":
                normalized.append("{emoji}")
            else:
                normalized.append(segment)
        return RouteKey(
            method=method.upper(),
            template="/" + "/".join(normalized),
            major_parameter=major_parameter,
        )

    def _record_application_pacing(self, context: RequestContext) -> None:
        interval = self._policies.get(context.policy, self._policies[READ_POLICY])
        policy_window = self._policy_windows.setdefault(
            context.policy, RateLimitWindow()
        )
        policy_window.last_request_at = context.started_at
        policy_window.next_allowed_at = max(
            policy_window.next_allowed_at,
            context.started_at + self._sample(interval),
        )

    def _record_family_pacing(self, context: RequestContext) -> float | None:
        family_window = self._family_windows.setdefault(
            context.family,
            RouteFamilyWindow(),
        )
        previous_request_at = family_window.last_request_at
        family_window.last_request_at = context.started_at
        if family_window.learned_interval > 0:
            family_window.next_allowed_at = max(
                family_window.next_allowed_at,
                context.started_at + family_window.learned_interval,
            )
        return previous_request_at

    def _learn_family_interval(
        self,
        *,
        context: RequestContext,
        previous_request_at: float | None,
        server_retry_after: float,
    ) -> float:
        family_window = self._family_windows.setdefault(
            context.family,
            RouteFamilyWindow(),
        )
        candidate = server_retry_after
        if previous_request_at is not None:
            elapsed = max(0.0, context.started_at - previous_request_at)
            recent_threshold = max(5.0, server_retry_after * 2)
            if elapsed <= recent_threshold:
                candidate += elapsed
        candidate += self._retry_jitter[0]
        family_window.learned_interval = max(
            family_window.learned_interval,
            candidate,
        )
        return family_window.learned_interval

    def _sample(self, interval: Interval) -> float:
        return self._random_between(*interval)

    @staticmethod
    def _validated_interval(interval: Interval) -> Interval:
        if len(interval) != 2:
            raise ValueError(
                "Request timing intervals must contain exactly two values."
            )
        minimum, maximum = (float(interval[0]), float(interval[1]))
        if (
            not math.isfinite(minimum)
            or not math.isfinite(maximum)
            or minimum < 0
            or maximum < minimum
        ):
            raise ValueError(
                "Request timing intervals must be finite, non-negative, and ordered."
            )
        return minimum, maximum

    @staticmethod
    def _header(headers: Mapping[str, Any], name: str) -> Any:
        value = headers.get(name)
        if value is not None:
            return value
        expected = name.lower()
        for key, item in headers.items():
            if str(key).lower() == expected:
                return item
        return None

    def _reset_after(self, headers: Mapping[str, Any]) -> float | None:
        reset_after = self._optional_non_negative_float(
            self._header(headers, "X-RateLimit-Reset-After")
        )
        if reset_after is not None:
            return reset_after
        return self._absolute_reset_delay(headers)

    def _retry_hint(
        self,
        headers: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> float | None:
        retry_after_header = self._header(headers, "Retry-After")
        relative_candidates = [
            self._optional_non_negative_float(payload.get("retry_after")),
            self._optional_non_negative_float(retry_after_header),
            self._optional_non_negative_float(
                self._header(headers, "X-RateLimit-Reset-After")
            ),
        ]
        valid_relative = [
            candidate for candidate in relative_candidates if candidate is not None
        ]
        if valid_relative:
            return max(valid_relative)

        absolute_candidates = [
            self._http_date_delay(retry_after_header),
            self._absolute_reset_delay(headers),
        ]
        valid_absolute = [
            candidate for candidate in absolute_candidates if candidate is not None
        ]
        return max(valid_absolute) if valid_absolute else None

    def _http_date_delay(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            retry_at = parsedate_to_datetime(str(value))
            if retry_at is None:
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, retry_at.timestamp() - self._wall_clock())
        except (OverflowError, TypeError, ValueError):
            return None

    def _absolute_reset_delay(self, headers: Mapping[str, Any]) -> float | None:
        reset_at = self._optional_non_negative_float(
            self._header(headers, "X-RateLimit-Reset")
        )
        if reset_at is None:
            return None
        return max(0.0, reset_at - self._wall_clock())

    @classmethod
    def _rate_limit_scope(
        cls, headers: Mapping[str, Any], payload: Mapping[str, Any]
    ) -> str | None:
        scope = cls._header(headers, "X-RateLimit-Scope")
        is_global = payload.get("global") is True or cls._is_true(
            cls._header(headers, "X-RateLimit-Global")
        )
        if is_global or str(scope).lower() == "global":
            return "global"
        return str(scope).lower() if scope is not None else None

    @staticmethod
    def _json_object(response: Any) -> Mapping[str, Any]:
        try:
            payload = response.json()
        except Exception:
            return {}
        return payload if isinstance(payload, Mapping) else {}

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_non_negative_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) and parsed >= 0 else None

    @staticmethod
    def _is_true(value: Any) -> bool:
        return value is True or str(value).lower() == "true"
