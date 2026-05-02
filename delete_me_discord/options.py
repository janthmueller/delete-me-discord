import argparse
import json
import sys
from datetime import timedelta

from .app_config import CLEAN_ARG_DEFAULTS, build_clean_defaults, load_profile
from .auth import DEFAULT_CONFIG_PATH
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


class CountFromZeroAction(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs):
        kwargs.setdefault("nargs", 0)
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        current = getattr(namespace, self.dest, None)
        setattr(namespace, self.dest, 1 if current is None else current + 1)


def _optional_time_delta(value: str):
    if value in {"none", "None"}:
        return None
    return parse_time_delta(value)


def _optional_non_negative_int(value: str):
    if value in {"none", "None"}:
        return None
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer or 'none'")
    return parsed


def _argv_has_json(argv) -> bool:
    if argv is None:
        argv = sys.argv[1:]
    return "--json" in argv or "-j" in argv


def _clean_default(name: str, clean_defaults: dict[str, object] | None = None):
    return (clean_defaults or CLEAN_ARG_DEFAULTS)[name]


def _boolean_action(clean_defaults: dict[str, object] | None = None):
    return argparse.BooleanOptionalAction if clean_defaults is not None else "store_true"


def _common_output_parent(*, clean_defaults: dict[str, object] | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "-q", "--quiet",
        action=_boolean_action(clean_defaults),
        default=_clean_default("quiet", clean_defaults),
        help="Only show warnings and errors."
    )
    parser.add_argument(
        "-v", "--verbose",
        action=CountFromZeroAction if clean_defaults is not None else "count",
        default=None if clean_defaults is not None else _clean_default("verbose", clean_defaults),
        help="Increase output detail. Repeat for more verbosity."
    )
    parser.add_argument(
        "-j", "--json",
        action=_boolean_action(clean_defaults),
        default=_clean_default("json", clean_defaults),
        help="Emit JSON output (logs and discovery output)."
    )
    parser.add_argument(
        "--redact-sensitive",
        nargs="*",
        default=_clean_default("redact_sensitive", clean_defaults),
        metavar="N",
        help="Redact sensitive values in normal logs. Without values, fully masks them. Provide two integers like '--redact-sensitive 0 4' to keep part of IDs visible."
    )
    return parser


def _config_parent(*, clean_defaults: dict[str, object] | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--config-path",
        type=str,
        default=_clean_default("config_path", clean_defaults) if clean_defaults else DEFAULT_CONFIG_PATH,
        help="Override config path (default: ~/.config/delete-me-discord/config.json)."
    )
    return parser


def _auth_parent(*, clean_defaults: dict[str, object] | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False, parents=[_config_parent(clean_defaults=clean_defaults)])
    parser.add_argument(
        "-t", "--token",
        type=str,
        default=_clean_default("token", clean_defaults),
        help="Discord token to use for this command. Overrides stored config and DISCORD_TOKEN."
    )
    return parser


def _api_parent(*, clean_defaults: dict[str, object] | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--max-retries",
        type=int,
        default=_clean_default("max_retries", clean_defaults),
        help="Maximum number of retries for API requests in case of rate limiting. Default is 5."
    )
    parser.add_argument(
        "--retry-time-buffer",
        nargs="+",
        default=_clean_default("retry_time_buffer", clean_defaults),
        metavar=("MIN", "MAX"),
        help="Additional time (in seconds) to wait after rate limit responses. Provide one value or two values for randomness. Default is [25, 35]."
    )
    return parser


def build_parser(
    version: str,
    json_output: bool = False,
    clean_defaults: dict[str, object] | None = None,
) -> argparse.ArgumentParser:
    output_parent = _common_output_parent()
    clean_output_parent = _common_output_parent(clean_defaults=clean_defaults)
    config_parent = _config_parent()
    auth_parent = _auth_parent()
    clean_auth_parent = _auth_parent(clean_defaults=clean_defaults)
    api_parent = _api_parent()
    clean_api_parent = _api_parent(clean_defaults=clean_defaults)

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
        parents=[clean_output_parent, clean_auth_parent, clean_api_parent],
    )
    clean_parser.add_argument(
        "-d", "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=_clean_default("dry_run", clean_defaults),
        help="Perform a dry run without deleting any messages."
    )
    clean_parser.add_argument(
        "--profile",
        type=str,
        default=_clean_default("profile", clean_defaults),
        help="Load cleanup defaults from a named profile in config.json."
    )
    clean_parser.add_argument(
        "-i", "--include-ids",
        type=str,
        nargs="*",
        default=_clean_default("include_ids", clean_defaults),
        help="List of channel IDs to include."
    )
    clean_parser.add_argument(
        "-x", "--exclude-ids",
        type=str,
        nargs="*",
        default=_clean_default("exclude_ids", clean_defaults),
        help="List of channel IDs to exclude."
    )
    clean_parser.add_argument(
        "-n", "--keep-last",
        type=int,
        default=_clean_default("keep_last", clean_defaults),
        metavar="N",
        help="Always keep the last N messages in each channel. Default is 0."
    )
    clean_parser.add_argument(
        "--keep-last-scope",
        type=str,
        default=_clean_default("keep_last_scope", clean_defaults),
        choices=["mine", "all"],
        help="How to count the kept last N messages: only your deletable messages ('mine') or all recent messages in the channel ('all'). Default is 'all'."
    )
    clean_parser.add_argument(
        "-k", "--keep-within",
        type=parse_time_delta,
        default=_clean_default("keep_within", clean_defaults),
        help="Keep messages and reactions newer than this time delta. Default is 0."
    )
    clean_parser.add_argument(
        "-f", "--fetch-within",
        type=_optional_time_delta,
        default=_clean_default("fetch_within", clean_defaults),
        help="Only fetch messages newer than this time delta from now. Use 'none' to disable any profile-defined limit."
    )
    clean_parser.add_argument(
        "-m", "--max-messages",
        type=_optional_non_negative_int,
        default=_clean_default("max_messages", clean_defaults),
        help="Maximum number of messages to fetch per channel. Use 'none' to disable any profile-defined limit."
    )
    clean_parser.add_argument(
        "--buffer-per-channel",
        action=argparse.BooleanOptionalAction,
        default=_clean_default("buffer_per_channel", clean_defaults),
        help="Fetch and buffer one channel fully before evaluation."
    )
    clean_parser.add_argument(
        "--keep-reactions",
        action=argparse.BooleanOptionalAction,
        default=_clean_default("keep_reactions", clean_defaults),
        help="Keep your reactions instead of removing them during cleanup."
    )
    clean_parser.add_argument(
        "--preserve-cache",
        action=argparse.BooleanOptionalAction,
        default=_clean_default("preserve_cache", clean_defaults),
        help="Enable preserve cache to re-fetch kept message IDs between runs."
    )
    clean_parser.add_argument(
        "--preserve-cache-path",
        type=str,
        default=_clean_default("preserve_cache_path", clean_defaults),
        help="Override preserve cache path (default: ~/.config/delete-me-discord/preserve_cache.json)."
    )
    clean_parser.add_argument(
        "--fetch-sleep-time",
        nargs="+",
        default=_clean_default("fetch_sleep_time", clean_defaults),
        metavar=("MIN", "MAX"),
        help="Sleep time (in seconds) between message fetch requests. Provide one value or two values for randomness. Default is [0.2, 0.4]."
    )
    clean_parser.add_argument(
        "--delete-sleep-time",
        nargs="+",
        default=_clean_default("delete_sleep_time", clean_defaults),
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
    list_subparsers.add_parser(
        "profiles",
        help="List available cleanup profiles from config.json.",
        parents=[output_parent, config_parent],
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
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    json_output = _argv_has_json(raw_argv)
    bootstrap_args = _bootstrap_parse(raw_argv)
    clean_defaults = None
    if bootstrap_args.command == "clean":
        profile_defaults = None
        try:
            if bootstrap_args.profile:
                profile_defaults = load_profile(bootstrap_args.config_path, bootstrap_args.profile)
        except ValueError as exc:
            parser = build_parser(version, json_output=json_output)
            parser.error(str(exc))
        clean_defaults = build_clean_defaults(bootstrap_args.profile, profile_defaults)
        json_output = json_output or bool(clean_defaults.get("json"))

    parser = build_parser(version, json_output=json_output, clean_defaults=clean_defaults)
    args = parser.parse_args(raw_argv)
    if getattr(args, "command", None) == "clean" and args.verbose is None:
        args.verbose = _clean_default("verbose", clean_defaults)
    if isinstance(args.redact_sensitive, list):
        try:
            args.redact_sensitive = parse_redaction_spec(args.redact_sensitive)
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))
    return args


def _bootstrap_parse(argv: list[str]):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-j", "--json", action="store_true")
    parser.add_argument("--config-path", default=DEFAULT_CONFIG_PATH)
    subparsers = parser.add_subparsers(dest="command")

    clean_parser = subparsers.add_parser("clean", add_help=False)
    clean_parser.add_argument("--config-path", default=DEFAULT_CONFIG_PATH)
    clean_parser.add_argument("--profile", default=None)

    for command_name in ("login", "logout", "whoami"):
        command_parser = subparsers.add_parser(command_name, add_help=False)
        command_parser.add_argument("--config-path", default=DEFAULT_CONFIG_PATH)

    list_parser = subparsers.add_parser("list", add_help=False)
    list_subparsers = list_parser.add_subparsers(dest="list_command")
    guilds_parser = list_subparsers.add_parser("guilds", add_help=False)
    guilds_parser.add_argument("--config-path", default=DEFAULT_CONFIG_PATH)
    channels_parser = list_subparsers.add_parser("channels", add_help=False)
    channels_parser.add_argument("--config-path", default=DEFAULT_CONFIG_PATH)
    profiles_parser = list_subparsers.add_parser("profiles", add_help=False)
    profiles_parser.add_argument("--config-path", default=DEFAULT_CONFIG_PATH)

    cache_parser = subparsers.add_parser("cache", add_help=False)
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command")
    cache_subparsers.add_parser("clear", add_help=False)

    return parser.parse_known_args(argv)[0]
