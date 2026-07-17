from pathlib import Path

import pytest

from tests.live.live_suite import (
    load_account_tokens,
    require_fixture_roles,
    validate_account_tokens,
)


@pytest.fixture(scope="session")
def live_account_report(pytestconfig):
    secret_file = Path(pytestconfig.getoption("--live-secrets-file"))
    expected_accounts = pytestconfig.getoption("--live-expected-accounts")
    tokens = require_fixture_roles(load_account_tokens(secret_file))
    report = validate_account_tokens(tokens)
    report.require_ready(expected_accounts)
    return report
