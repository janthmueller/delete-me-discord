import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

from .api import DiscordAPI
from .app_config import EffectiveCleanSettings, load_profile_names, resolve_effective_clean_settings
from .auth import resolve_token, run_auth_command
from .cleaner import MessageCleaner
from .discovery import run_discovery_commands
from .options import parse_args
from .preserve_cache import PreserveCache
from .privacy import sensitive
from .utils import AuthenticationError, parse_random_range, setup_logging

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


def _clear_preserve_cache(preserve_cache_path: str) -> None:
    try:
        if os.path.exists(preserve_cache_path):
            os.remove(preserve_cache_path)
            logging.info("Deleted preserve cache at %s.", sensitive(preserve_cache_path, full=True))
        else:
            logging.info("No preserve cache found at %s.", sensitive(preserve_cache_path, full=True))
    except Exception as exc:
        logging.error("Failed to delete preserve cache at %s: %s", sensitive(preserve_cache_path, full=True), exc)


def _build_api_from_token_config(
    token_arg: str | None,
    config_path: str,
    max_retries: int,
    retry_time_buffer,
) -> DiscordAPI:
    token, _token_source = resolve_token(token_arg, config_path)
    if not token:
        logging.error("Discord token not provided. Use --token, dmd login, or set DISCORD_TOKEN.")
        raise SystemExit(1)

    return DiscordAPI(
        token=token,
        max_retries=max_retries,
        retry_time_buffer=retry_time_buffer,
    )


def _build_api(args) -> DiscordAPI:
    return _build_api_from_token_config(
        token_arg=args.token,
        config_path=args.config_path,
        max_retries=args.max_retries,
        retry_time_buffer=parse_random_range(args.retry_time_buffer, "retry-time-buffer"),
    )


def _build_api_from_settings(settings: EffectiveCleanSettings) -> DiscordAPI:
    return _build_api_from_token_config(
        token_arg=settings.token,
        config_path=settings.config_path,
        max_retries=settings.max_retries,
        retry_time_buffer=settings.retry_time_buffer,
    )


def _run_clean(settings: EffectiveCleanSettings) -> None:
    if settings.keep_last < 0:
        logging.error("--keep-last must be a non-negative integer.")
        raise SystemExit(1)

    fetch_since = None
    if settings.fetch_within:
        fetch_since = datetime.now(timezone.utc) - settings.fetch_within

    api = _build_api_from_settings(settings)

    try:
        current_user = api.get_current_user()
    except AuthenticationError as e:
        logging.error("Authentication failed (invalid token?): %s", e)
        raise SystemExit(1)

    user_id = current_user.get("id")
    if not user_id:
        logging.error("Authentication failed: user ID missing in /users/@me response.")
        raise SystemExit(1)
    logging.info(
        "Authenticated as %s (%s).",
        sensitive(current_user.get("username", "unknown"), full=True),
        sensitive(user_id),
    )

    preserve_cache = PreserveCache(
        path=settings.preserve_cache_path,
    ) if settings.preserve_cache else None

    cleaner = MessageCleaner(
        api=api,
        user_id=user_id,
        include_ids=settings.include_ids,
        exclude_ids=settings.exclude_ids,
        preserve_last=settings.keep_within,
        preserve_n=settings.keep_last,
        preserve_n_mode=settings.keep_last_scope,
        preserve_cache=preserve_cache,
    )

    cleaner.clean_messages(
        dry_run=settings.dry_run,
        fetch_sleep_time_range=settings.fetch_sleep_time,
        delete_sleep_time_range=settings.delete_sleep_time,
        fetch_since=fetch_since,
        max_messages=settings.max_messages if settings.max_messages is not None else float("inf"),
        buffer_channel_messages=settings.buffer_per_channel,
        delete_reactions=not settings.keep_reactions,
    )
    if preserve_cache:
        preserve_cache.save()
        logging.info("Preserve cache saved to %s.", sensitive(settings.preserve_cache_path, full=True))


def _run_list(args) -> None:
    api = _build_api(args)
    list_guilds = args.list_command == "guilds"
    list_channels = args.list_command == "channels"
    run_discovery_commands(
        api=api,
        list_guilds=list_guilds,
        list_channels=list_channels,
        include_ids=[],
        exclude_ids=[],
        json_output=args.json,
    )


def _run_list_profiles(args) -> None:
    try:
        profile_names = load_profile_names(args.config_path)
    except ValueError as exc:
        _emit_config_error_and_exit(str(exc), getattr(args, "json", False))
    if args.json:
        print(json.dumps(profile_names, ensure_ascii=True))
        return
    if not profile_names:
        print("No profiles configured.")
        return
    for name in profile_names:
        print(name)


def _resolve_clean_settings_or_exit(args) -> EffectiveCleanSettings:
    try:
        return resolve_effective_clean_settings(args)
    except (ValueError, argparse.ArgumentTypeError) as exc:
        _emit_config_error_and_exit(str(exc), getattr(args, "json", False))


def _emit_config_error_and_exit(message: str, json_output: bool) -> None:
    if json_output:
        payload = {
            "error": message,
            "type": "config_error",
        }
        print(json.dumps(payload, ensure_ascii=True))
    else:
        print(f"Configuration error: {message}", file=sys.stderr)
    raise SystemExit(1)


def main():
    """
    The main function orchestrating the message cleaning process.
    """
    args = parse_args(__version__)

    effective_clean_settings = None
    if args.command == "clean":
        effective_clean_settings = _resolve_clean_settings_or_exit(args)

    # Configure logging based on user input
    setup_logging(
        verbosity=getattr(effective_clean_settings or args, "verbose", 0),
        quiet=getattr(effective_clean_settings or args, "quiet", False),
        json_output=(effective_clean_settings.json if effective_clean_settings else args.json),
        redaction_config=getattr(effective_clean_settings or args, "redact_sensitive", None),
    )

    try:
        if getattr(args, "command", None) in {"login", "logout", "whoami"}:
            run_auth_command(args)
            return
        if args.command == "clean":
            _run_clean(effective_clean_settings)
            return
        if args.command == "list":
            if args.list_command == "profiles":
                _run_list_profiles(args)
                return
            _run_list(args)
            return
        if args.command == "cache" and args.cache_command == "clear":
            _clear_preserve_cache(args.preserve_cache_path)
            return
        raise ValueError(f"Unsupported command: {args.command}")
    except SystemExit:
        raise
    except Exception as exc:
        if args.json:
            payload = {
                "error": str(exc),
                "type": "exception",
                "exception": exc.__class__.__name__,
            }
            print(json.dumps(payload, ensure_ascii=True))
            raise SystemExit(1)
        raise

if __name__ == "__main__":
    main()
