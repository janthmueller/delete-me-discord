"""Guild and channel discovery collection and presentation."""

from .renderers import (
    render_channels_json,
    render_channels_rich,
    render_guilds_json,
    render_guilds_rich,
)
from .service import (
    collect_channels,
    collect_channels_from_inventory,
    collect_guilds,
    collect_guilds_from_inventory,
    run_discovery_commands,
)

__all__ = [
    "collect_channels",
    "collect_channels_from_inventory",
    "collect_guilds",
    "collect_guilds_from_inventory",
    "render_channels_json",
    "render_channels_rich",
    "render_guilds_json",
    "render_guilds_rich",
    "run_discovery_commands",
]
