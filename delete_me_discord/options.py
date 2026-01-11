# delete_me_discord/options.py
import argparse
import json
import sys
from datetime import timedelta

from .utils import parse_time_delta
from .preserve_cache import DEFAULT_PRESERVE_CACHE_PATH


class JsonArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, json_output: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._json_output = json_output

    def error(self, message):
        if self._json_output:
            payload = {
                "error": message,
                "type": "argument_error",
            }
            print(json.dumps(payload, ensure_ascii=True))
            raise SystemExit(2)
        super().error(message)


def _argv_has_json(argv) -> bool:
    if argv is None:
        argv = sys.argv[1:]
    return "--json" in argv


def build_parser(version: str, json_output: bool = False) -> argparse.ArgumentParser:
    """
    Build the CLI argument parser.
    """
    parser = JsonArgumentParser(
        description="Delete Discord messages older than a specified time delta.",
        json_output=json_output,
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {version}",
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
        "--json",
        action="store_true",
        help="Emit JSON output (logs and discovery output)."
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
        "--preserve-n-mode",
        type=str,
        default="mine",
        choices=["mine", "all"],
        help="How to count the last N messages to keep: 'mine' (only your deletable messages) or 'all' (last N messages in the channel, any author). Default is 'mine'."
    )
    parser.add_argument(
        "-p", "--preserve-last",
        type=parse_time_delta,
        default=timedelta(weeks=2),
        help="Preserves recent messages (and reactions) within last given delta time (e.g., 'weeks=2,days=3' or '2w3d') regardless of --preserve-n. Default is weeks=2."
    )
    parser.add_argument(
        "-a", "--fetch-max-age",
        type=parse_time_delta,
        default=None,
        help="Only fetch messages newer than this time delta from now (e.g., 'weeks=1,days=3' or '10d'). Speeds up recurring purges by skipping older history. Defaults to no max age."
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
        help="Remove your reactions on messages encountered once the deletion window is reached (older than the cutoff and past the preserve-n threshold)."
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
    parser.add_argument(
        "--preserve-cache",
        action='store_true',
        help="Enable preserve cache to re-fetch preserved messages between runs."
    )
    parser.add_argument(
        "--wipe-preserve-cache",
        action='store_true',
        help="Delete the preserve cache file and exit."
    )
    parser.add_argument(
        "--preserve-cache-path",
        type=str,
        default=DEFAULT_PRESERVE_CACHE_PATH,
        help="Override preserve cache path (default: ~/.config/delete-me-discord/preserve_cache.json)."
    )
    return parser


def parse_args(version: str, argv=None):
    """
    Parse CLI arguments using the provided version string.
    """
    json_output = _argv_has_json(argv)
    parser = build_parser(version, json_output=json_output)
    return parser.parse_args(argv)
