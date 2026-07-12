# delete_me_discord/discovery.py
from typing import Dict, Any, List
from rich.console import Console

from .api import DiscordAPI
from .channel_types import (
    ChannelType,
    GUILD_MESSAGE_CHANNEL_TYPES,
    ROOT_MESSAGE_CHANNEL_TYPES,
    THREAD_CHANNEL_TYPES,
    THREAD_CONTAINER_CHANNEL_TYPES,
    channel_type_name,
    channel_type_sort_order,
    is_archived_thread,
)
from .scope_inventory import ScopeInventory
from .scope_filter import ScopeFilter
from .utils import should_include_channel
from .discovery_renderers import (
    render_guilds_json,
    render_guilds_rich,
    render_channels_json,
    render_channels_rich,
)


def _guild_sort_key(guild):
    return ((guild.get("name") or "").lower(), guild.get("id"))


def run_discovery_commands(
    api: DiscordAPI,
    list_guilds: bool,
    list_channels: bool,
    include_ids,
    exclude_ids,
    json_output: bool = False,
    inventory: ScopeInventory | None = None,
) -> None:
    """
    Handle discovery-only commands and exit afterwards.
    """
    include_set = set(include_ids or [])
    exclude_set = set(exclude_ids or [])
    console = None
    if not json_output:
        console = Console()
    if list_guilds:
        guilds = (
            collect_guilds_from_inventory(inventory, include_set, exclude_set)
            if inventory
            else collect_guilds(api, include_set, exclude_set)
        )
        if json_output:
            render_guilds_json(guilds)
        else:
            render_guilds_rich(guilds, console)

    if list_channels:
        inventory = inventory or ScopeInventory.fetch(api)
        data = collect_channels_from_inventory(inventory, include_set, exclude_set)
        if json_output:
            render_channels_json(data)
        else:
            render_channels_rich(data, console)


def collect_guilds(
    api: DiscordAPI,
    include_set,
    exclude_set
) -> List[Dict[str, Any]]:
    """
    Collect guilds respecting include/exclude filters.
    """
    guilds = api.get_guilds()
    inventory = ScopeInventory(guilds=guilds, root_channels=[], guild_channels_by_guild={})
    return collect_guilds_from_inventory(inventory, include_set, exclude_set)


def collect_guilds_from_inventory(
    inventory: ScopeInventory,
    include_set,
    exclude_set,
) -> List[Dict[str, Any]]:
    """
    Collect guilds respecting include/exclude filters from a fetched scope inventory.
    """
    items = []
    for guild in sorted(inventory.guilds, key=_guild_sort_key):
        guild_id = guild.get("id")
        if guild_id in exclude_set:
            continue
        if include_set and guild_id not in include_set:
            continue
        items.append({
            "id": guild_id,
            "name": guild.get("name", "Unknown"),
        })
    return items


def collect_channels(
    api: DiscordAPI,
    include_set,
    exclude_set,
    *,
    scope_filter: ScopeFilter | None = None,
) -> Dict[str, Any]:
    """
    Collect channels grouped by DMs and guilds, respecting include/exclude filters.
    """
    inventory = ScopeInventory.fetch(
        api,
        scope_filter=scope_filter,
    )
    return collect_channels_from_inventory(inventory, include_set, exclude_set)


def collect_channels_from_inventory(
    inventory: ScopeInventory,
    include_set,
    exclude_set,
) -> Dict[str, Any]:
    """
    Collect channels grouped by DMs and guilds from a fetched scope inventory.
    """
    def include_channel(channel):
        return should_include_channel(
            channel=channel,
            include_ids=include_set,
            exclude_ids=exclude_set,
            scope_filter=inventory.scope_filter,
        )

    def channel_sort_key(channel):
        name = channel.get("name")
        if not name:
            recipients = channel.get("recipients") or []
            name = ', '.join([recipient.get("username", "Unknown") for recipient in recipients])
        return (channel_type_sort_order(channel.get("type")), name.lower(), channel.get("id"))

    included_dms = []
    for channel in inventory.root_channels:
        if channel.get("type") not in ROOT_MESSAGE_CHANNEL_TYPES:
            continue
        if not include_channel(channel):
            continue
        included_dms.append(channel)

    dms = []
    for channel in sorted(included_dms, key=channel_sort_key):
        dms.append({
            "id": channel.get("id"),
            "name": channel.get("name") or ', '.join(
                [recipient.get("username", "Unknown") for recipient in channel.get("recipients", [])]
            ),
            "type": channel_type_name(channel.get("type")),
        })

    json_guilds = []
    for guild in sorted(inventory.guilds, key=_guild_sort_key):
        guild_id = guild.get("id")
        guild_name = guild.get("name", "Unknown")

        channels = inventory.guild_channels(guild_id)
        threads = inventory.guild_threads(guild_id)
        parent_by_id = {
            str(channel["id"]): channel
            for channel in channels
            if channel.get("id") is not None
        }

        category_names = {
            c.get("id"): c.get("name") or "Unknown category"
            for c in channels
            if c.get("type") == ChannelType.GUILD_CATEGORY
        }

        candidate_channels = [
            channel
            for channel in channels
            if channel.get("type") in GUILD_MESSAGE_CHANNEL_TYPES
            or (
                channel.get("type") in THREAD_CONTAINER_CHANNEL_TYPES
                and inventory.scope_filter.searches_thread_parent(channel.get("type"))
            )
        ]
        if inventory.includes_threads:
            candidate_channels.extend(
                thread for thread in threads if thread.get("type") in THREAD_CHANNEL_TYPES
            )

        filtered_channels = []
        for channel in candidate_channels:
            if channel.get("type") == ChannelType.GUILD_CATEGORY:
                continue
            if not include_channel(channel):
                continue
            filtered_channels.append(channel)

        if not filtered_channels:
            continue

        grouped = {}
        for channel in filtered_channels:
            category_id = (
                channel.get("category_id")
                if channel.get("type") in THREAD_CHANNEL_TYPES
                else channel.get("parent_id")
            )
            grouped.setdefault(category_id, []).append(channel)

        categories = []

        def category_label(parent_id):
            return category_names.get(parent_id, "(no category)")

        for category_id, chans in sorted(grouped.items(), key=lambda item: (category_label(item[0]).lower(), item[0] or "")):
            entries = []
            for channel in sorted(chans, key=channel_sort_key):
                entry = {
                    "id": channel.get("id"),
                    "name": channel.get("name") or ', '.join(
                        [recipient.get("username", "Unknown") for recipient in channel.get("recipients", [])]
                    ),
                    "type": channel_type_name(channel.get("type")),
                }
                if channel.get("type") in THREAD_CHANNEL_TYPES:
                    thread_parent_id = channel.get("parent_id")
                    parent = parent_by_id.get(str(thread_parent_id)) if thread_parent_id is not None else None
                    entry.update({
                        "parent_id": thread_parent_id,
                        "parent_name": (parent or {}).get("name") or "Unknown parent",
                        "archived": is_archived_thread(channel),
                    })
                entries.append(entry)
            categories.append({
                "id": category_id,
                "name": category_label(category_id),
                "channels": entries,
            })
        json_guilds.append({
            "id": guild_id,
            "name": guild_name,
            "categories": categories,
        })

    return {"dms": dms, "guilds": json_guilds}
