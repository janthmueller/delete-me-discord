"""Human-readable Discord object labels for logs and reports."""

from collections.abc import Mapping
from typing import Any

from ..privacy import sensitive, sensitive_name
from .channel_types import channel_type_name


def channel_str(channel: Mapping[str, Any]) -> str:
    """Return a redaction-aware channel type, name, and ID label."""
    channel_type = channel_type_name(channel.get("type"))
    channel_name = channel.get("name") or ", ".join(
        recipient.get("username", "Unknown")
        for recipient in channel.get("recipients", [])
    )
    return (
        f"{channel_type} {sensitive_name(channel_name)} "
        f"(ID: {sensitive(channel.get('id', 'unknown'))})"
    )


__all__ = ["channel_str"]
