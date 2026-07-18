"""Failures translated from Discord HTTP and authentication responses."""


class AuthenticationError(Exception):
    """The active credential was rejected by Discord."""


class ReachedMaxRetries(Exception):
    """A retryable request exhausted its configured attempts."""


class ResourceUnavailable(Exception):
    """A Discord resource is inaccessible or absent."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class UnexpectedStatus(Exception):
    """Discord returned an unhandled HTTP or API status."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        discord_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.discord_code = discord_code
