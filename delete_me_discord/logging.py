"""Application log levels shared by core services and the CLI."""

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol, cast


PROGRESS_LEVEL = 15
EVENT_LEVEL = 14
DETAIL_LEVEL = 13
DIAGNOSTIC_LEVEL = 12

_LEVEL_METHODS = {
    "progress": PROGRESS_LEVEL,
    "event": EVENT_LEVEL,
    "detail": DETAIL_LEVEL,
    "diagnostic": DIAGNOSTIC_LEVEL,
}


class ApplicationLogger(Protocol):
    def debug(self, message: object, *args: Any, **kwargs: Any) -> None: ...

    def info(self, message: object, *args: Any, **kwargs: Any) -> None: ...

    def warning(self, message: object, *args: Any, **kwargs: Any) -> None: ...

    def error(self, message: object, *args: Any, **kwargs: Any) -> None: ...

    def exception(self, message: object, *args: Any, **kwargs: Any) -> None: ...

    def critical(self, message: object, *args: Any, **kwargs: Any) -> None: ...

    def progress(self, message: object, *args: Any, **kwargs: Any) -> None: ...

    def event(self, message: object, *args: Any, **kwargs: Any) -> None: ...

    def detail(self, message: object, *args: Any, **kwargs: Any) -> None: ...

    def diagnostic(self, message: object, *args: Any, **kwargs: Any) -> None: ...


def _log_at_level(level: int) -> Callable[..., None]:
    def _inner(
        self: logging.Logger,
        message: object,
        *args: object,
        indent: int = 0,
        prefix: str = "",
        no_wrap: bool = False,
        **kwargs: Any,
    ) -> None:
        if not self.isEnabledFor(level):
            return
        extra = dict(kwargs.pop("extra", {}) or {})
        extra["cli_indent"] = indent
        extra["cli_prefix"] = prefix
        extra["cli_no_wrap"] = no_wrap
        self._log(level, message, args, extra=extra, **kwargs)

    return _inner


def install_logging_extensions() -> None:
    """Register the custom levels and logger methods once per process."""
    for method_name, level in _LEVEL_METHODS.items():
        logging.addLevelName(level, method_name.upper())
        if not hasattr(logging.Logger, method_name):
            setattr(logging.Logger, method_name, _log_at_level(level))


def get_logger(name: str | None = None) -> ApplicationLogger:
    """Return a standard logger with the installed application extensions."""
    return cast(ApplicationLogger, logging.getLogger(name))


def format_timestamp(value: datetime) -> str:
    """Return a compact, consistent UTC timestamp for log output."""
    return value.astimezone(timezone.utc).strftime("[%y/%m/%d %H:%M:%S]")


install_logging_extensions()


__all__ = [
    "ApplicationLogger",
    "DETAIL_LEVEL",
    "DIAGNOSTIC_LEVEL",
    "EVENT_LEVEL",
    "PROGRESS_LEVEL",
    "get_logger",
    "format_timestamp",
    "install_logging_extensions",
]
