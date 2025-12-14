# delete_me_discord/__init__.py

from .api import DiscordAPI, FetchError
from .cleaner import MessageCleaner
from .utils import setup_logging, parse_random_range, parse_time_delta, should_include_channel
from datetime import timedelta, datetime, timezone

import argparse
import logging
from rich.console import Console
from rich.tree import Tree
from rich.markup import escape


def _guild_sort_key(guild):
    return ((guild.get("name") or "").lower(), guild.get("id"))

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
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show the version number and exit."
    )
    parser.add_argument(
        "-i", "--include-ids",
        type=str,
        nargs='*',
        default=[],
        help="List of channel/guild/parent IDs to include."
    )
    parser.add_argument(
        "-x", "--exclude-ids",
        type=str,
        nargs='*',
        default=[],
        help="List of channel/guild/parent IDs to exclude."
    )
    parser.add_argument(
        "-d", "--dry-run",
        action='store_true',
        help="Perform a dry run without deleting any messages."
    )
    parser.add_argument(
        "-l", "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level. Default is 'INFO'."
    )
    parser.add_argument(
        "-r", "--max-retries",
        type=int,
        default=5,
        help="Maximum number of retries for API requests in case of rate limiting. Default is 5."
    )
    parser.add_argument(
        "-b", "--retry-time-buffer",
        nargs='+',
        default=[25, 35],
        metavar=('MIN', 'MAX'),
        help="Additional time (in seconds) to wait after rate limit responses. Provide one value or two values for randomness. Default is [25, 35]."
    )
    parser.add_argument(
        "-f", "--fetch-sleep-time",
        nargs='+',
        default=[0.2, 0.4],
        metavar=('MIN', 'MAX'),
        help="Sleep time (in seconds) between message fetch requests. Provide one value or two values for randomness. Default is [0.2, 0.4]."
    )
    parser.add_argument(
        "-s", "--delete-sleep-time",
        nargs='+',
        default=[1.5, 2],
        metavar=('MIN', 'MAX'),
        help="Sleep time (in seconds) between message deletion attempts. Provide one value or two values for randomness. Default is [1.5, 2]."
    )
    parser.add_argument(
        "-n", "--preserve-n",
        type=int,
        default=12,
        metavar='N',
        help="Number of recent messages to preserve in each channel regardless of --preserve-last. Default is 12."
    )
    parser.add_argument(
        "-p", "--preserve-last",
        type=parse_time_delta,
        default=timedelta(weeks=2),
        help="Preserves recent messages (and reactions) within last given delta time 'weeks=2,days=3' regardless of --preserve-n. Default is weeks=2."
    )
    parser.add_argument(
        "-a", "--fetch-max-age",
        type=parse_time_delta,
        default=None,
        help="Only fetch messages newer than this time delta from now (e.g., 'weeks=1,days=2'). Speeds up recurring purges by skipping older history. Defaults to no max age."
    )
    parser.add_argument(
        "-m", "--max-messages",
        type=int,
        default=None,
        help="Maximum number of messages to fetch per channel. Defaults to no limit."
    )
    parser.add_argument(
        "-R", "--delete-reactions",
        action='store_true',
        help="Remove your reactions from messages encountered (even if messages are preserved or not deletable)."
    )
    parser.add_argument(
        "-g", "--list-guilds",
        action='store_true',
        help="List guild IDs and names, then exit."
    )
    parser.add_argument(
        "-c", "--list-channels",
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
    retry_time_buffer_range = parse_random_range(args.retry_time_buffer, "retry-time-buffer")
    fetch_sleep_time_range = parse_random_range(args.fetch_sleep_time, "fetch-sleep-time")
    delete_sleep_time_range = parse_random_range(args.delete_sleep_time, "delete-sleep-time")
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

        try:
            current_user = api.get_current_user()
        except FetchError as e:
            logging.error("Authentication failed (invalid token?): %s", e)
            return

        user_id = current_user.get("id")
        if not user_id:
            logging.error("Authentication failed: user ID missing in /users/@me response.")
            return
        logging.info("Authenticated as %s (%s).", current_user.get("username"), user_id)

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
            user_id=user_id,
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
    console = Console()
    include_set = set(include_ids or [])
    exclude_set = set(exclude_ids or [])

    if list_guilds:
        try:
            guilds = api.get_guilds()
        except FetchError as e:
            logging.error("Unable to list guilds: %s", e)
            return
        tree = Tree("[blue]Guilds[/]")
        for guild in sorted(guilds, key=_guild_sort_key):
            guild_id = guild.get("id")
            if guild_id in exclude_set:
                continue
            if include_set and guild_id not in include_set:
                continue
            tree.add(f"[bright_white]{escape(guild.get('name', 'Unknown'))}[/] [dim](ID: {guild_id})[/]")
        if tree.children:
            console.print(tree)
        else:
            console.print("[dim]No guilds matched filters for this account.[/]")

    if list_channels:
        _list_channels(api, include_set, exclude_set, console)

def _list_channels(api: DiscordAPI, include_set, exclude_set, console: Console) -> None:
    """
    List channels grouped by DMs and guilds, respecting include/exclude filters.
    """
    channel_types = {0: "GuildText", 1: "DM", 3: "GroupDM"}

    def include_channel(channel):
        return should_include_channel(
            channel=channel,
            include_ids=include_set,
            exclude_ids=exclude_set
        )

    def channel_sort_key(channel):
        type_order = {0: 0, 1: 1, 3: 2}  # GuildText, DM, GroupDM
        name = channel.get("name")
        if not name:
            recipients = channel.get("recipients") or []
            name = ', '.join([recipient.get("username", "Unknown") for recipient in recipients])
        return (type_order.get(channel.get("type"), 99), name.lower(), channel.get("id"))

    def channel_display(channel):
        channel_type = channel_types.get(channel.get("type"), f"Type {channel.get('type')}")
        raw_name = channel.get("name") or ', '.join(
            [recipient.get("username", "Unknown") for recipient in channel.get("recipients", [])]
        )
        channel_name = escape(raw_name)
        type_color = "cyan"
        name_style = "bright_white"
        id_style = "dim"
        return f"[{type_color}]{channel_type}[/] [{name_style}]{channel_name}[/] [{id_style}](ID: {channel.get('id')})[/]"

    dm_tree = None
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
        dm_tree = Tree("[magenta]Direct and Group DMs[/]")
        for channel in sorted(included_dms, key=channel_sort_key):
            dm_tree.add(channel_display(channel))

    # Guild channels
    try:
        guilds = api.get_guilds()
    except FetchError as e:
        logging.error("Unable to list guild channels: %s", e)
        return

    guilds_tree = None
    for guild in sorted(guilds, key=_guild_sort_key):
        guild_id = guild.get("id")
        guild_name = guild.get("name", "Unknown")
        escaped_guild_name = escape(guild_name)

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

        if guilds_tree is None:
            guilds_tree = Tree("[blue]Guilds[/]")

        guild_node = guilds_tree.add(f"[bright_white]{escaped_guild_name}[/] [dim](ID: {guild_id})[/]")
        grouped = {}
        for channel in filtered_channels:
            grouped.setdefault(channel.get("parent_id"), []).append(channel)

        def category_label(parent_id):
            return category_names.get(parent_id, "(no category)")

        for parent_id, chans in sorted(grouped.items(), key=lambda item: (category_label(item[0]).lower(), item[0] or "")):
            parent_label = category_label(parent_id)
            category_node = guild_node.add(f"[yellow]Category[/] {escape(parent_label)} [dim](ID: {parent_id or 'none'})[/]")
            for channel in sorted(chans, key=channel_sort_key):
                category_node.add(channel_display(channel))

    printed = False
    if dm_tree and dm_tree.children:
        console.print(dm_tree)
        printed = True
    if guilds_tree and guilds_tree.children:
        console.print(guilds_tree)
        printed = True
    if not printed:
        console.print("[dim]No channels matched filters for this account.[/]")

if __name__ == "__main__":
    main()
