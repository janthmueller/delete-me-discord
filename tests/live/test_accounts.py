def test_configured_accounts_are_valid_and_distinct(live_account_report, pytestconfig):
    expected_accounts = pytestconfig.getoption("--live-expected-accounts")

    assert len(live_account_report.checks) == expected_accounts
    assert live_account_report.valid_count == expected_accounts
