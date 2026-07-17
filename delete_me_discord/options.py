import argparse
import json
import sys

from .app_config import (
    CLEAN_ARG_DEFAULTS,
    build_clean_defaults,
    load_profile,
    profile_requests_json_output,
)
from .auth import DEFAULT_CONFIG_PATH
from .channel_types import FILTERABLE_CHANNEL_TYPE_NAMES, OWNED_THREAD_DELETE_MODES
from .preserve_cache import DEFAULT_PRESERVE_CACHE_PATH
from .privacy import RedactionConfig
from .rate_limits import REQUEST_POLICY_DEFAULTS
from .scope_filter import THREAD_STATES
from .scope_selectors import parse_scope_selectors
from .utils import parse_random_range, parse_redaction_spec, parse_time_delta


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


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _request_interval(value: str) -> tuple[str, tuple[float, float]]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected POLICY=MIN or POLICY=MIN,MAX")
    policy, raw_interval = (part.strip() for part in value.split("=", 1))
    if policy not in REQUEST_POLICY_DEFAULTS:
        expected = ", ".join(sorted(REQUEST_POLICY_DEFAULTS))
        raise argparse.ArgumentTypeError(
            f"unknown request policy '{policy}'; expected one of: {expected}"
        )
    values = [part for part in raw_interval.replace(",", " ").split() if part]
    try:
        interval = parse_random_range(values, f"request-interval {policy}")
    except argparse.ArgumentTypeError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return policy, interval


def _argv_json_setting(argv) -> bool | None:
    if argv is None:
        argv = sys.argv[1:]
    enabled = None
    for token in argv:
        if token in {"-j", "--json"}:
            enabled = True
        elif token == "--no-json":
            enabled = False
    return enabled


def _clean_default(name: str, clean_defaults: dict[str, object] | None = None):
    return (clean_defaults or CLEAN_ARG_DEFAULTS)[name]


def _common_output_parent(*, clean_defaults: dict[str, object] | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "-q", "--quiet",
        action=argparse.BooleanOptionalAction,
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
        action=argparse.BooleanOptionalAction,
        default=_clean_default("json", clean_defaults),
        help="Emit JSON output (logs and discovery output)."
    )
    parser.add_argument(
        "-r", "--redact-sensitive",
        nargs="*",
        default=_clean_default("redact_sensitive", clean_defaults),
        metavar="N",
        help="Redact sensitive values in logs and discovery output. Without values, fully masks them. Provide one suffix integer like '-r 4' or two integers like '0 4' to keep part of IDs visible."
    )
    parser.add_argument(
        "--redact-names",
        action=argparse.BooleanOptionalAction,
        default=_clean_default("redact_names", clean_defaults),
        help="Redact human-readable names when sensitive redaction is enabled."
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
        help="Discord token to use for this command. Overrides stored credentials and DISCORD_TOKEN."
    )
    return parser


def _scope_parent(
    *,
    clean_defaults: dict[str, object] | None = None,
    channel_filter_options: bool = True,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "-i", "--include", "--include-ids",
        dest="include_selectors",
        type=str,
        nargs="*",
        default=_clean_default("include_ids", clean_defaults),
        metavar="SELECTOR",
        help=(
            "Include complete Discord IDs, channel types, 'threads', or thread "
            "states ('active'/'archived')."
        ),
    )
    parser.add_argument(
        "-x", "--exclude", "--exclude-ids",
        dest="exclude_selectors",
        type=str,
        nargs="*",
        default=_clean_default("exclude_ids", clean_defaults),
        metavar="SELECTOR",
        help=(
            "Exclude complete Discord IDs, channel types, 'threads', or thread "
            "states ('active'/'archived')."
        ),
    )
    if channel_filter_options:
        parser.add_argument(
            "--exclude-channel-types",
            nargs="*",
            choices=FILTERABLE_CHANNEL_TYPE_NAMES,
            default=_clean_default("exclude_channel_types", clean_defaults),
            metavar="TYPE",
            help="Exclude message-bearing Discord channel types from discovery and cleanup."
        )
        parser.add_argument(
            "--exclude-thread-states",
            nargs="*",
            choices=THREAD_STATES,
            default=_clean_default("exclude_thread_states", clean_defaults),
            metavar="STATE",
            help="Exclude active or archived threads from discovery and cleanup."
        )
        parser.add_argument(
            "--exclude-threads",
            action=argparse.BooleanOptionalAction,
            default=_clean_default("exclude_threads", clean_defaults),
            help="Exclude all announcement, public, and private threads."
        )
    return parser


def _api_parent(*, clean_defaults: dict[str, object] | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    policy_names = ", ".join(sorted(REQUEST_POLICY_DEFAULTS))
    parser.add_argument(
        "--max-retries",
        type=_non_negative_int,
        default=_clean_default("max_retries", clean_defaults),
        help="Maximum number of retries for API requests in case of rate limiting. Default is 5."
    )
    parser.add_argument(
        "--retry-safety-jitter",
        "--retry-time-buffer",
        dest="retry_time_buffer",
        nargs="+",
        default=_clean_default("retry_time_buffer", clean_defaults),
        metavar=("MIN", "MAX"),
        help="Small jitter (in seconds) added to server-provided retry delays. Provide one value or two values for randomness. Default is [0.1, 0.3]."
    )
    parser.add_argument(
        "--request-interval",
        action="append",
        type=_request_interval,
        default=list(_clean_default("request_intervals", clean_defaults)),
        metavar="POLICY=MIN[,MAX]",
        help=(
            "Override a named minimum request interval. Repeat for multiple policies. "
            f"Available policies: {policy_names}."
        )
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
    scope_parent = _scope_parent()
    guild_scope_parent = _scope_parent(channel_filter_options=False)
    clean_scope_parent = _scope_parent(clean_defaults=clean_defaults)
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
        parents=[clean_output_parent, clean_auth_parent, clean_scope_parent, clean_api_parent],
    )
    clean_parser.add_argument(
        "-d", "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=_clean_default("dry_run", clean_defaults),
        help=(
            "Plan cleanup and deletion-cascade impact without deleting messages, "
            "reactions, or thread containers, and without changing thread state."
        )
    )
    clean_parser.add_argument(
        "--profile",
        type=str,
        default=_clean_default("profile", clean_defaults),
        help="Load cleanup defaults from a named profile in config.json."
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
        "--delete-owned-threads",
        choices=OWNED_THREAD_DELETE_MODES,
        default=_clean_default("delete_owned_threads", clean_defaults),
        metavar="MODE",
        help=(
            "Delete thread channels created by you: 'self-only' requires a complete scan with no "
            "messages from other authors at scan time, but may still remove their reactions; 'all' "
            "deletes the thread including other users' messages. Default is 'none'."
        ),
    )
    clean_parser.add_argument(
        "--skip-unrestorable-threads",
        action=argparse.BooleanOptionalAction,
        default=_clean_default("skip_unrestorable_threads", clean_defaults),
        help=(
            "Skip archived threads when restoring their archived state cannot "
            "be reasonably guaranteed."
        ),
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
        help="Minimum interval (in seconds) between message fetch requests. Provide one value or two values for randomness. Default is [0.2, 0.4]."
    )
    clean_parser.add_argument(
        "--delete-sleep-time",
        nargs="+",
        default=_clean_default("delete_sleep_time", clean_defaults),
        metavar=("MIN", "MAX"),
        help="Minimum interval (in seconds) between cleanup mutation requests. Provide one value or two values for randomness. Default is [1.5, 2]."
    )

    list_parser = subparsers.add_parser(
        "list",
        help="Discover available targets and supported scope-filter values.",
    )
    list_subparsers = list_parser.add_subparsers(dest="list_command", required=True)
    list_subparsers.add_parser(
        "guilds",
        help="List guild IDs and names.",
        parents=[output_parent, auth_parent, guild_scope_parent, api_parent],
    )
    list_subparsers.add_parser(
        "channels",
        help="List channels grouped by guild/category/parent plus DMs.",
        parents=[output_parent, auth_parent, scope_parent, api_parent],
    )
    list_subparsers.add_parser(
        "profiles",
        help="List available cleanup profiles from config.json.",
        parents=[output_parent, config_parent],
    )
    list_subparsers.add_parser(
        "channel-types",
        help="List channel type names accepted by --exclude-channel-types.",
        parents=[output_parent],
    )
    list_subparsers.add_parser(
        "thread-states",
        help="List thread state names accepted by --exclude-thread-states.",
        parents=[output_parent],
    )

    profile_parser = subparsers.add_parser(
        "profile",
        help="Inspect and manage cleanup profiles stored in config.json.",
    )
    profile_subparsers = profile_parser.add_subparsers(dest="profile_command", required=True)
    profile_subparsers.add_parser(
        "fields",
        help="List available stored profile fields and value types.",
        parents=[output_parent, config_parent],
    )
    profile_show_parser = profile_subparsers.add_parser(
        "show",
        help="Show a stored cleanup profile.",
        parents=[output_parent, config_parent],
    )
    profile_show_parser.add_argument("name", type=str, help="Profile name.")

    profile_add_parser = profile_subparsers.add_parser(
        "add",
        help="Create a new cleanup profile.",
        parents=[output_parent, auth_parent, api_parent],
    )
    profile_add_parser.add_argument("name", type=str, help="Profile name.")
    profile_add_parser.add_argument(
        "--set",
        dest="profile_set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Set a stored profile field such as keep_last=20 or preserve_cache=true.",
    )

    profile_update_parser = profile_subparsers.add_parser(
        "update",
        help="Update an existing cleanup profile.",
        parents=[output_parent, auth_parent, api_parent],
    )
    profile_update_parser.add_argument("name", type=str, help="Profile name.")
    profile_update_parser.add_argument(
        "--set",
        dest="profile_set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Set a stored profile field such as keep_last=20 or preserve_cache=true.",
    )
    profile_update_parser.add_argument(
        "--unset",
        dest="profile_unset",
        nargs="+",
        default=[],
        metavar="FIELD",
        help="Unset one or more stored profile fields such as fetch_within max_messages.",
    )

    profile_remove_parser = profile_subparsers.add_parser(
        "remove",
        help="Remove an existing cleanup profile.",
        parents=[output_parent, config_parent],
    )
    profile_remove_parser.add_argument("name", type=str, help="Profile name.")

    login_parser = subparsers.add_parser(
        "login",
        help="Validate a Discord token and store it in the system keyring.",
        parents=[output_parent, config_parent, api_parent],
    )
    login_parser.add_argument(
        "--replace",
        action="store_true",
        help="Prompt for a new token even if a stored token already exists.",
    )
    login_parser.set_defaults(command="login")

    logout_parser = subparsers.add_parser(
        "logout",
        help="Remove the stored Discord token from the system keyring.",
        parents=[output_parent, config_parent],
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

    profile_parser = subparsers.add_parser("profile", add_help=False)
    profile_subparsers = profile_parser.add_subparsers(dest="profile_command")
    for profile_command in ("fields", "show", "add", "update", "remove"):
        profile_command_parser = profile_subparsers.add_parser(profile_command, add_help=False)
        profile_command_parser.add_argument("--config-path", default=DEFAULT_CONFIG_PATH)

    list_parser = subparsers.add_parser("list", add_help=False)
    list_subparsers = list_parser.add_subparsers(dest="list_command")
    guilds_parser = list_subparsers.add_parser("guilds", add_help=False)
    guilds_parser.add_argument("--config-path", default=DEFAULT_CONFIG_PATH)
    channels_parser = list_subparsers.add_parser("channels", add_help=False)
    channels_parser.add_argument("--config-path", default=DEFAULT_CONFIG_PATH)
    profiles_parser = list_subparsers.add_parser("profiles", add_help=False)
    profiles_parser.add_argument("--config-path", default=DEFAULT_CONFIG_PATH)
    list_subparsers.add_parser("channel-types", add_help=False)
    list_subparsers.add_parser("thread-states", add_help=False)

    cache_parser = subparsers.add_parser("cache", add_help=False)
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command")
    cache_subparsers.add_parser("clear", add_help=False)

    return parser.parse_known_args(argv)[0]


def _resolve_bootstrap_clean_json_output(
    raw_argv: list[str],
    config_path: str,
    profile_name: str | None,
) -> bool:
    explicit_json_setting = _argv_json_setting(raw_argv)
    if explicit_json_setting is not None:
        return explicit_json_setting
    if profile_name:
        return profile_requests_json_output(config_path, profile_name)
    return False


def parse_args(version: str, argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    bootstrap_args = _bootstrap_parse(raw_argv)
    json_output = _resolve_bootstrap_clean_json_output(
        raw_argv,
        bootstrap_args.config_path,
        getattr(bootstrap_args, "profile", None),
    ) if bootstrap_args.command == "clean" else (_argv_json_setting(raw_argv) is True)
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
    if hasattr(args, "include_selectors"):
        try:
            selectors = parse_scope_selectors(
                args.include_selectors,
                args.exclude_selectors,
            )
        except ValueError as exc:
            parser.error(str(exc))
        if (
            getattr(args, "list_command", None) == "guilds"
            and (
                selectors.included_channel_types
                or selectors.excluded_channel_types
                or selectors.included_thread_states
                or selectors.excluded_thread_states
                or selectors.include_threads
                or selectors.exclude_threads
            )
        ):
            parser.error("dmd list guilds accepts only complete Discord ID selectors")
        args.include_ids = list(selectors.include_ids)
        args.exclude_ids = list(selectors.exclude_ids)
        args.include_channel_types = list(selectors.included_channel_types)
        args.include_thread_states = list(selectors.included_thread_states)
        args.include_threads = selectors.include_threads
        if hasattr(args, "exclude_channel_types"):
            args.exclude_channel_types = list(dict.fromkeys([
                *args.exclude_channel_types,
                *selectors.excluded_channel_types,
            ]))
            args.exclude_thread_states = list(dict.fromkeys([
                *args.exclude_thread_states,
                *selectors.excluded_thread_states,
            ]))
            args.exclude_threads = args.exclude_threads or selectors.exclude_threads
        del args.include_selectors
        del args.exclude_selectors
    if hasattr(args, "request_interval"):
        request_intervals = {}
        for policy, interval in args.request_interval:
            if policy in request_intervals:
                parser.error(f"request policy '{policy}' was overridden more than once")
            request_intervals[policy] = interval
        args.request_intervals = request_intervals
        del args.request_interval
    if getattr(args, "command", None) == "clean" and args.verbose is None:
        args.verbose = _clean_default("verbose", clean_defaults)
    if isinstance(args.redact_sensitive, list):
        try:
            args.redact_sensitive = parse_redaction_spec(args.redact_sensitive)
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))
    if args.redact_sensitive is not None:
        args.redact_sensitive = RedactionConfig(
            enabled=args.redact_sensitive.enabled,
            prefix=args.redact_sensitive.prefix,
            suffix=args.redact_sensitive.suffix,
            redact_names=args.redact_names,
            mask=args.redact_sensitive.mask,
        )
    return args
