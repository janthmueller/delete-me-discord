import argparse
import json
import sys
from datetime import timedelta

from .auth import DEFAULT_AUTH_CONFIG_PATH
from .preserve_cache import DEFAULT_PRESERVE_CACHE_PATH
from .utils import parse_redaction_spec, parse_time_delta


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
    return "--json" in argv or "-j" in argv


def _common_output_parent() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Only show warnings and errors."
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase output detail. Repeat for more verbosity."
    )
    parser.add_argument(
        "-j", "--json",
        action="store_true",
        help="Emit JSON output (logs and discovery output)."
    )
    parser.add_argument(
        "--redact-sensitive",
        nargs="*",
        default=None,
        metavar="N",
        help="Redact sensitive values in normal logs. Without values, fully masks them. Provide two integers like '--redact-sensitive 0 4' to keep part of IDs visible."
    )
    return parser


def _auth_parent() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "-t", "--token",
        type=str,
        default=None,
        help="Discord token to use for this command. Overrides stored config and DISCORD_TOKEN."
    )
    parser.add_argument(
        "--auth-config-path",
        type=str,
        default=DEFAULT_AUTH_CONFIG_PATH,
        help="Override auth config path (default: ~/.config/delete-me-discord/config.json)."
    )
    return parser


def _api_parent() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Maximum number of retries for API requests in case of rate limiting. Default is 5."
    )
    parser.add_argument(
        "--retry-time-buffer",
        nargs="+",
        default=[25, 35],
        metavar=("MIN", "MAX"),
        help="Additional time (in seconds) to wait after rate limit responses. Provide one value or two values for randomness. Default is [25, 35]."
    )
    return parser


def build_parser(version: str, json_output: bool = False) -> argparse.ArgumentParser:
    output_parent = _common_output_parent()
    auth_parent = _auth_parent()
    api_parent = _api_parent()

    parser = JsonArgumentParser(
        description="Delete your Discord messages and reactions with explicit filters and retention controls.",
        json_output=json_output,
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"%(prog)s {version}",
        help="Show the version number and exit."
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    clean_parser = subparsers.add_parser(
        "clean",
        help="Delete messages and optionally reactions within the selected scope.",
        parents=[output_parent, auth_parent, api_parent],
    )
    clean_parser.add_argument(
        "-d", "--dry-run",
        action="store_true",
        help="Perform a dry run without deleting any messages."
    )
    clean_parser.add_argument(
        "-i", "--include-ids",
        type=str,
        nargs="*",
        default=[],
        help="List of channel IDs to include."
    )
    clean_parser.add_argument(
        "-x", "--exclude-ids",
        type=str,
        nargs="*",
        default=[],
        help="List of channel IDs to exclude."
    )
    clean_parser.add_argument(
        "-n", "--keep-last",
        type=int,
        default=0,
        metavar="N",
        help="Always keep the last N messages in each channel. Default is 0."
    )
    clean_parser.add_argument(
        "--keep-last-scope",
        type=str,
        default="all",
        choices=["mine", "all"],
        help="How to count the kept last N messages: only your deletable messages ('mine') or all recent messages in the channel ('all'). Default is 'all'."
    )
    clean_parser.add_argument(
        "-k", "--keep-within",
        type=parse_time_delta,
        default=timedelta(0),
        help="Keep messages and reactions newer than this time delta. Default is 0."
    )
    clean_parser.add_argument(
        "-f", "--fetch-within",
        type=parse_time_delta,
        default=None,
        help="Only fetch messages newer than this time delta from now. Defaults to no fetch limit."
    )
    clean_parser.add_argument(
        "-m", "--max-messages",
        type=int,
        default=None,
        help="Maximum number of messages to fetch per channel. Defaults to no limit."
    )
    clean_parser.add_argument(
        "--buffer-per-channel",
        action="store_true",
        help="Fetch and buffer one channel fully before evaluation."
    )
    clean_parser.add_argument(
        "--keep-reactions",
        action="store_true",
        help="Keep your reactions instead of removing them during cleanup."
    )
    clean_parser.add_argument(
        "--preserve-cache",
        action="store_true",
        help="Enable preserve cache to re-fetch kept message IDs between runs."
    )
    clean_parser.add_argument(
        "--preserve-cache-path",
        type=str,
        default=DEFAULT_PRESERVE_CACHE_PATH,
        help="Override preserve cache path (default: ~/.config/delete-me-discord/preserve_cache.json)."
    )
    clean_parser.add_argument(
        "--fetch-sleep-time",
        nargs="+",
        default=[0.2, 0.4],
        metavar=("MIN", "MAX"),
        help="Sleep time (in seconds) between message fetch requests. Provide one value or two values for randomness. Default is [0.2, 0.4]."
    )
    clean_parser.add_argument(
        "--delete-sleep-time",
        nargs="+",
        default=[1.5, 2],
        metavar=("MIN", "MAX"),
        help="Sleep time (in seconds) between message deletion attempts. Provide one value or two values for randomness. Default is [1.5, 2]."
    )

    list_parser = subparsers.add_parser(
        "list",
        help="Discover guilds or channels available to the authenticated user.",
    )
    list_subparsers = list_parser.add_subparsers(dest="list_command", required=True)
    list_subparsers.add_parser(
        "guilds",
        help="List guild IDs and names.",
        parents=[output_parent, auth_parent, api_parent],
    )
    list_subparsers.add_parser(
        "channels",
        help="List channels grouped by guild/category/parent plus DMs.",
        parents=[output_parent, auth_parent, api_parent],
    )

    login_parser = subparsers.add_parser(
        "login",
        help="Store and validate a Discord token.",
        parents=[output_parent, auth_parent, api_parent],
    )
    login_parser.set_defaults(command="login")

    logout_parser = subparsers.add_parser(
        "logout",
        help="Remove the stored Discord token.",
        parents=[output_parent, auth_parent],
    )
    logout_parser.set_defaults(command="logout")

    whoami_parser = subparsers.add_parser(
        "whoami",
        help="Show which account the active token belongs to.",
        parents=[output_parent, auth_parent, api_parent],
    )
    whoami_parser.set_defaults(command="whoami")

    cache_parser = subparsers.add_parser(
        "cache",
        help="Manage the preserve cache.",
        parents=[output_parent],
    )
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command", required=True)
    cache_clear_parser = cache_subparsers.add_parser("clear", help="Delete the preserve cache file.")
    cache_clear_parser.add_argument(
        "--preserve-cache-path",
        type=str,
        default=DEFAULT_PRESERVE_CACHE_PATH,
        help="Override preserve cache path (default: ~/.config/delete-me-discord/preserve_cache.json)."
    )

    return parser


def parse_args(version: str, argv=None):
    json_output = _argv_has_json(argv)
    parser = build_parser(version, json_output=json_output)
    args = parser.parse_args(argv)
    if args.redact_sensitive is not None:
        try:
            args.redact_sensitive = parse_redaction_spec(args.redact_sensitive)
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))
    return args
