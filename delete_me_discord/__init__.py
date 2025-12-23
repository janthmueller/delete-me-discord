# delete_me_discord/__init__.py

from .api import DiscordAPI, FetchError
from .cleaner import MessageCleaner
from .discovery import run_discovery_commands
from .options import parse_args
from .utils import setup_logging, parse_random_range
from datetime import datetime, timezone

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
    args = parse_args(__version__)

    # Configure logging based on user input
    setup_logging(log_level=args.log_level)

    include_ids = args.include_ids
    exclude_ids = args.exclude_ids
    preserve_last = args.preserve_last
    preserve_n = args.preserve_n
    preserve_n_mode = args.preserve_n_mode
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
            run_discovery_commands(
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
            preserve_n=preserve_n,
            preserve_n_mode=preserve_n_mode
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

if __name__ == "__main__":
    main()
