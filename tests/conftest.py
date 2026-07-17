import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LIVE_TEST_ROOT = PROJECT_ROOT / "tests" / "live"


def pytest_addoption(parser):
    group = parser.getgroup("live Discord suite")
    group.addoption(
        "--live-discord",
        action="store_true",
        help="Enable opt-in tests that make requests to dedicated Discord fixtures.",
    )
    group.addoption(
        "--live-destructive",
        action="store_true",
        help="Enable destructive live tests in addition to read-only live tests.",
    )
    group.addoption(
        "--live-secrets-file",
        default=str(LIVE_TEST_ROOT / "secrets.env"),
        help="Path to the owner-only TOKEN_* file used by live tests.",
    )
    group.addoption(
        "--live-expected-accounts",
        type=int,
        default=4,
        help="Required number of distinct live test accounts.",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: makes requests to dedicated Discord fixtures")
    config.addinivalue_line(
        "markers",
        "live_destructive: mutates dedicated Discord fixtures and requires explicit opt-in",
    )
    if config.getoption("--live-destructive") and not config.getoption("--live-discord"):
        raise pytest.UsageError("--live-destructive requires --live-discord")
    if config.getoption("--live-discord") and config.getoption("showlocals"):
        raise pytest.UsageError("--showlocals is forbidden for the live Discord suite")


def pytest_collection_modifyitems(config, items):
    live_enabled = config.getoption("--live-discord")
    destructive_enabled = config.getoption("--live-destructive")
    live_skip = pytest.mark.skip(reason="live Discord suite requires --live-discord")
    destructive_skip = pytest.mark.skip(
        reason="destructive live tests require --live-destructive"
    )

    for item in items:
        item_path = Path(str(item.path)).resolve()
        if item_path.is_relative_to(LIVE_TEST_ROOT):
            item.add_marker(pytest.mark.live)
        if "live" in item.keywords and not live_enabled:
            item.add_marker(live_skip)
        if "live_destructive" in item.keywords and not destructive_enabled:
            item.add_marker(destructive_skip)
