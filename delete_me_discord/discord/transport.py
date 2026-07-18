"""HTTP transport, retry handling, and response normalization for Discord."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

import requests

from .errors import (
    AuthenticationError,
    ReachedMaxRetries,
    ResourceUnavailable,
    UnexpectedStatus,
)
from .rate_limits import (
    READ_POLICY,
    DiscordRequestScheduler,
    WaitSnapshot,
)


class DiscordTransport:
    """Execute Discord HTTP requests under shared pacing and retry policy."""

    def __init__(
        self,
        token: Optional[str] = None,
        max_retries: int = 5,
        retry_time_buffer: Tuple[float, float] = (0.1, 0.3),
        request_timeout: Tuple[float, float] = (10.0, 30.0),
        request_intervals: Optional[Mapping[str, Tuple[float, float]]] = None,
        request_scheduler: Optional[DiscordRequestScheduler] = None,
    ) -> None:
        resolved_token = token or os.getenv("DISCORD_TOKEN")
        if not resolved_token:
            raise ValueError(
                "Discord token not provided. Set the DISCORD_TOKEN environment variable."
            )

        self.max_retries = max_retries
        self.request_timeout = request_timeout
        self._request_interval_overrides = set(request_intervals or {})
        if request_scheduler is None:
            self.request_scheduler = DiscordRequestScheduler(
                retry_jitter=retry_time_buffer,
                policy_intervals=request_intervals,
            )
        else:
            self.request_scheduler = request_scheduler
            for name, interval in (request_intervals or {}).items():
                self.request_scheduler.configure_policy(name, interval)

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": resolved_token,
                "Content-Type": "application/json",
            }
        )
        self.logger: Any = logging.getLogger(self.__class__.__name__)

    def configure_policy(
        self,
        name: str,
        interval: Tuple[float, float],
    ) -> None:
        """Set application pacing unless explicitly overridden at construction."""
        if name in self._request_interval_overrides:
            return
        self.request_scheduler.configure_policy(name, interval)

    def wait_snapshot(self, policy: str) -> WaitSnapshot:
        """Return cumulative scheduler waits for one request policy."""
        return self.request_scheduler.wait_snapshot(policy)

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self.session.close()

    @staticmethod
    def _retry_backoff(attempt: int) -> float:
        return min(2 ** max(0, attempt - 1), 30)

    def request(
        self,
        url: str,
        description: str,
        method: str = "get",
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        pacing_policy: str = READ_POLICY,
        expected_statuses: Optional[Set[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute one normalized Discord request with bounded retries."""
        if method not in {"get", "delete", "patch"}:
            raise ValueError(f"Unsupported Discord HTTP method: {method}.")
        success_codes = expected_statuses or {
            "get": {200},
            "delete": {204},
            "patch": {200},
        }[method]
        attempts = 0
        while attempts <= self.max_retries:
            request_context = self.request_scheduler.acquire(
                method,
                url,
                policy=pacing_policy,
            )
            if request_context.waited_seconds > 0:
                self.logger.diagnostic(
                    "Waited %.2f seconds before attempting to %s (%s).",
                    request_context.waited_seconds,
                    description,
                    ", ".join(request_context.wait_reasons),
                )
            try:
                request_kwargs: Dict[str, Any] = {
                    "method": method,
                    "url": url,
                    "params": params,
                    "timeout": self.request_timeout,
                }
                if json_body is not None:
                    request_kwargs["json"] = json_body
                response = self.session.request(**request_kwargs)
            except requests.RequestException as exc:
                attempts += 1
                retry_after = self.request_scheduler.fallback_retry_delay(
                    self._retry_backoff(attempts)
                )
                self.request_scheduler.defer_route(
                    request_context,
                    retry_after,
                    record_attempt=True,
                )
                if attempts > self.max_retries:
                    break
                self.logger.diagnostic(
                    "Network error while attempting to %s (%s). Retry scheduled in %.2f seconds.",
                    description,
                    exc,
                    retry_after,
                )
                continue

            rate_limit = self.request_scheduler.observe(
                request_context,
                response,
                retry_fallback=self._retry_backoff(attempts + 1),
            )
            if response.status_code in {200, 204}:
                if response.status_code not in success_codes:
                    raise UnexpectedStatus(
                        f"Unexpected status {response.status_code} for "
                        f"{method.upper()} while attempting to {description}.",
                        status_code=response.status_code,
                    )
                if response.status_code == 204:
                    return []
                data = response.json()
                if not isinstance(data, list):
                    data = [data]
                return data
            if response.status_code == 429:
                attempts += 1
                if attempts > self.max_retries:
                    break
                rate_limit_scope = rate_limit.scope or (
                    "bucket" if rate_limit.bucket_id is not None else "route"
                )
                if rate_limit.used_fallback:
                    self.logger.diagnostic(
                        "Rate limited while attempting to %s without a usable Discord retry delay. "
                        "Full-jitter fallback scheduled in %.2f seconds "
                        "(scope=%s, policy=%s).",
                        description,
                        rate_limit.retry_after,
                        rate_limit_scope,
                        request_context.policy,
                    )
                else:
                    self.logger.diagnostic(
                        "Rate limited while attempting to %s. Discord retry %.2f seconds + "
                        "%.2f seconds safety; retry scheduled in %.2f seconds "
                        "(scope=%s, policy=%s).",
                        description,
                        rate_limit.server_retry_after,
                        rate_limit.safety_jitter,
                        rate_limit.retry_after,
                        rate_limit_scope,
                        request_context.policy,
                    )
                if rate_limit.learned_family_interval > 0:
                    self.logger.diagnostic(
                        "Learned %.2f-second minimum interval for %s %s across major resources.",
                        rate_limit.learned_family_interval,
                        request_context.family.method,
                        request_context.family.template,
                    )
                continue
            if response.status_code == 202:
                try:
                    payload = response.json()
                except (TypeError, ValueError):
                    payload = None
                if isinstance(payload, dict) and payload.get("code") == 110000:
                    attempts += 1
                    retry_after = self.request_scheduler.retry_delay_for_response(
                        response,
                        fallback=self._retry_backoff(attempts),
                    )
                    self.request_scheduler.defer_route(request_context, retry_after)
                    if attempts > self.max_retries:
                        break
                    self.logger.diagnostic(
                        "Discord is indexing data while attempting to %s. "
                        "Retry scheduled in %.2f seconds.",
                        description,
                        retry_after,
                    )
                    continue
            if response.status_code == 408 or 500 <= response.status_code < 600:
                attempts += 1
                retry_after = self.request_scheduler.retry_delay_for_response(
                    response,
                    fallback=self._retry_backoff(attempts),
                )
                self.request_scheduler.defer_route(request_context, retry_after)
                if attempts > self.max_retries:
                    break
                self.logger.diagnostic(
                    "Retryable HTTP %s while attempting to %s. Retry scheduled in %.2f seconds.",
                    response.status_code,
                    description,
                    retry_after,
                )
                continue
            if response.status_code == 401:
                raise AuthenticationError(
                    f"Unauthorized while attempting to {description}. Status Code: 401"
                )
            if response.status_code in {403, 404}:
                raise ResourceUnavailable(
                    f"Resource unavailable while attempting to {description}. "
                    f"Status Code: {response.status_code}",
                    status_code=response.status_code,
                )
            try:
                error_payload = response.json()
            except (TypeError, ValueError):
                error_payload = None
            discord_code = (
                error_payload.get("code")
                if isinstance(error_payload, dict)
                and isinstance(error_payload.get("code"), int)
                else None
            )
            raise UnexpectedStatus(
                f"Unhandled status code {response.status_code} while attempting to {description}.",
                status_code=response.status_code,
                discord_code=discord_code,
            )

        raise ReachedMaxRetries(
            f"Max retries exceeded while attempting to {description}."
        )
