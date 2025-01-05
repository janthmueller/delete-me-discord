# delete_me_discord/__init__.py

from .api import DiscordAPI, FetchError
from .cleaner import MessageCleaner
from .utils import setup_logging, parse_random_range, parse_preserve_last
from datetime import timedelta

import argparse
import logging

__version__ = "0.0.5"

def main():
    """
    The main function orchestrating the message cleaning process.
    """
    parser = argparse.ArgumentParser(
        description="Delete Discord messages older than a specified time delta."
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
        type=parse_preserve_last,
        default=timedelta(weeks=2),
        help="Preserves recent messages within last given delta time 'weeks=2,days=3' regardless of --preserve-n. Default is weeks=2."
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

    if preserve_n < 0:
        logging.error("--preserve-n must be a non-negative integer.")
        return

    try:
        # Initialize DiscordAPI with max_retries and retry_time_buffer
        api = DiscordAPI(
            max_retries=max_retries,
            retry_time_buffer=retry_time_buffer_range
        )
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
            delete_sleep_time_range=delete_sleep_time_range
        )
        logging.info("Script completed. Total messages deleted: %s", total_deleted)
    except FetchError as e:
        logging.error("FetchError occurred: %s", e)
    except ValueError as e:
        logging.error("ValueError: %s", e)
    except Exception as e:
        logging.exception("An unexpected error occurred: %s", e)

if __name__ == "__main__":
    main()