# delete_me_discord/__init__.py

from .api import DiscordAPI, FetchError
from .cleaner import MessageCleaner
from .utils import setup_logging, parse_random_range, parse_time_delta, should_include_channel
from datetime import timedelta, datetime, timezone

import argparse
import logging

try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _version

    __version__ = _version("delete-me-discord")
except PackageNotFoundError:
    try:
        from setuptools_scm import get_version

        __version__ = get_version(root=".", relative_to=__file__)
    except Exception:
        __version__ = "0.0.0-dev"

def main():
    """
    The main function orchestrating the message cleaning process.
    """
    parser = argparse.ArgumentParser(
        description="Delete Discord messages older than a specified time delta."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show the version number and exit."
    )
    parser.add_argument(
        "--include-ids",
        type=str,
        nargs='*',
        default=[],
        help="List of channel/guild/parent IDs to include."
    )
    parser.add_argument(
        "--exclude-ids",
        type=str,
        nargs='*',
        default=[],
        help="List of channel/guild/parent IDs to exclude."
    )
    parser.add_argument(
        "--dry-run",
        action='store_true',
        help="Perform a dry run without deleting any messages."
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level. Default is 'INFO'."
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Maximum number of retries for API requests in case of rate limiting. Default is 5."
    )
    parser.add_argument(
        "--retry-time-buffer",
        type=lambda x: parse_random_range(x, "retry-time-buffer"),
        nargs='+',
        default=[25, 35],
        metavar=('MIN', 'MAX'),
        help="Additional time (in seconds) to wait after rate limit responses. Provide one value or two values for randomness. Default is [25, 35]."
    )
    parser.add_argument(
        "--fetch-sleep-time",
        type=lambda x: parse_random_range(x, "fetch-sleep-time"),
        nargs='+',
        default=[0.2, 0.4],
        metavar=('MIN', 'MAX'),
        help="Sleep time (in seconds) between message fetch requests. Provide one value or two values for randomness. Default is [0.2, 0.4]."
    )
    parser.add_argument(
        "--delete-sleep-time",
        type=lambda x: parse_random_range(x, "delete-sleep-time"),
        nargs='+',
        default=[1.5, 2],
        metavar=('MIN', 'MAX'),
        help="Sleep time (in seconds) between message deletion attempts. Provide one value or two values for randomness. Default is [1.5, 2]."
    )
    parser.add_argument(
        "--preserve-n",
        type=int,
        default=12,
        metavar='N',
        help="Number of recent messages to preserve in each channel regardless of --preserve-last. Default is 12."
    )
    parser.add_argument(
        "--preserve-last",
        type=parse_time_delta,
        default=timedelta(weeks=2),
        help="Preserves recent messages (and reactions) within last given delta time 'weeks=2,days=3' regardless of --preserve-n. Default is weeks=2."
    )
    parser.add_argument(
        "--fetch-max-age",
        type=parse_time_delta,
        default=None,
        help="Only fetch messages newer than this time delta from now (e.g., 'weeks=1,days=2'). Speeds up recurring purges by skipping older history. Defaults to no max age."
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=None,
        help="Maximum number of messages to fetch per channel. Defaults to no limit."
    )
    parser.add_argument(
        "--delete-reactions",
        action='store_true',
        help="Remove your reactions from messages encountered (even if messages are preserved or not deletable)."
    )
    parser.add_argument(
        "--list-guilds",
        action='store_true',
        help="List guild IDs and names, then exit."
    )
    parser.add_argument(
        "--list-channels",
        action='store_true',
        help="List channel IDs/types (grouped by guild/DMs), then exit."
    )
    args = parser.parse_args()

    # Configure logging based on user input
    setup_logging(log_level=args.log_level)

    include_ids = args.include_ids
    exclude_ids = args.exclude_ids
    preserve_last = args.preserve_last
    preserve_n = args.preserve_n
    dry_run = args.dry_run
    max_retries = args.max_retries
    retry_time_buffer_range = args.retry_time_buffer  # Tuple[float, float]
    fetch_sleep_time_range = args.fetch_sleep_time  # Tuple[float, float]
    delete_sleep_time_range = args.delete_sleep_time  # Tuple[float, float]
    fetch_max_age = args.fetch_max_age  # Optional[timedelta]
    max_messages = args.max_messages if args.max_messages is not None else float("inf")
    delete_reactions = args.delete_reactions
    list_guilds = args.list_guilds
    list_channels = args.list_channels

    fetch_since = None
    if fetch_max_age:
        fetch_since = datetime.now(timezone.utc) - fetch_max_age

    if preserve_n < 0:
        logging.error("--preserve-n must be a non-negative integer.")
        return

    try:
        # Initialize DiscordAPI with max_retries and retry_time_buffer
        api = DiscordAPI(
            max_retries=max_retries,
            retry_time_buffer=retry_time_buffer_range
        )

        if list_guilds or list_channels:
            _run_discovery_commands(
                api=api,
                list_guilds=list_guilds,
                list_channels=list_channels,
                include_ids=include_ids,
                exclude_ids=exclude_ids
            )
            return

        cleaner = MessageCleaner(
            api=api,
            include_ids=include_ids,
            exclude_ids=exclude_ids,
            preserve_last=preserve_last,
            preserve_n=preserve_n
        )

        # Start cleaning messages
        total_deleted = cleaner.clean_messages(
            dry_run=dry_run,
            fetch_sleep_time_range=fetch_sleep_time_range,
            delete_sleep_time_range=delete_sleep_time_range,
            fetch_since=fetch_since,
            max_messages=max_messages,
            delete_reactions=delete_reactions
        )
        logging.info("Script completed. Total messages deleted: %s", total_deleted)
    except FetchError as e:
        logging.error("FetchError occurred: %s", e)
    except ValueError as e:
        logging.error("ValueError: %s", e)
    except Exception as e:
        logging.exception("An unexpected error occurred: %s", e)

def _run_discovery_commands(
    api: DiscordAPI,
    list_guilds: bool,
    list_channels: bool,
    include_ids,
    exclude_ids
) -> None:
    """
    Handle discovery-only commands and exit afterwards.
    """
    include_set = set(include_ids or [])
    exclude_set = set(exclude_ids or [])

    if list_guilds:
        try:
            guilds = api.get_guilds()
        except FetchError as e:
            logging.error("Unable to list guilds: %s", e)
            return
        if not guilds:
            print("No guilds found for this account.")
        for guild in guilds:
            guild_id = guild.get("id")
            if guild_id in exclude_set:
                continue
            if include_set and guild_id not in include_set:
                continue
            print(f"Guild: {guild.get('name', 'Unknown')} (ID: {guild.get('id')})")

    if list_channels:
        _list_channels(api, include_set, exclude_set)

def _list_channels(api: DiscordAPI, include_set, exclude_set) -> None:
    """
    List channels grouped by DMs and guilds, respecting include/exclude filters.
    """
    channel_types = {0: "GuildText", 1: "DM", 3: "GroupDM"}

    def include_channel(channel):
        return should_include_channel(
            channel=channel,
            include_ids=set(include_set),
            exclude_ids=set(exclude_set)
        )

    def channel_display(channel):
        channel_type = channel_types.get(channel.get("type"), f"Type {channel.get('type')}")
        channel_name = channel.get("name") or ', '.join(
            [recipient.get("username", "Unknown") for recipient in channel.get("recipients", [])]
        )
        return f"[{channel_type}] {channel_name} (ID: {channel.get('id')})"

    try:
        root_channels = api.get_root_channels()
    except FetchError as e:
        logging.error("Unable to list DM/Group DM channels: %s", e)
        root_channels = []

    included_dms = []
    for channel in root_channels:
        if channel.get("type") not in channel_types:
            continue
        if not include_channel(channel):
            continue
        included_dms.append(channel)

    if included_dms:
        print("Direct and group DMs:")
        for channel in included_dms:
            print(f"  {channel_display(channel)}")

    try:
        guilds = api.get_guilds()
    except FetchError as e:
        logging.error("Unable to list guild channels: %s", e)
        return

    for guild in guilds:
        guild_id = guild.get("id")
        guild_name = guild.get("name", "Unknown")

        try:
            channels = api.get_guild_channels(guild_id)
        except FetchError as e:
            logging.error("  Failed to fetch channels for guild %s: %s", guild_id, e)
            continue

        category_names = {
            c.get("id"): c.get("name") or "Unknown category"
            for c in channels
            if c.get("type") == 4  # Category
        }

        filtered_channels = []
        for channel in channels:
            if channel.get("type") not in channel_types:
                continue
            if not include_channel(channel):
                continue
            filtered_channels.append(channel)

        if not filtered_channels:
            continue

        print(f"Guild {guild_name} (ID: {guild_id})")
        grouped = {}
        for channel in filtered_channels:
            grouped.setdefault(channel.get("parent_id"), []).append(channel)

        for parent_id, chans in grouped.items():
            parent_label = category_names.get(parent_id, "(no category)")
            print(f"  Category {parent_label} (ID: {parent_id or 'none'})")
            for channel in chans:
                print(f"    {channel_display(channel)}")

if __name__ == "__main__":
    main()
