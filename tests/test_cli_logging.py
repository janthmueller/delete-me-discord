# delete-me-discord CLI logging tests
import json
import logging
import sys
from pathlib import Path

import pytest

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.cli.logging import JsonLogFormatter
from delete_me_discord.logging import structured_event
from delete_me_discord.privacy import RedactionConfig, sensitive, set_redaction_config


def test_json_log_formatter_outputs_required_keys():
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=12,
        msg="hello",
        args=(),
        exc_info=None,
    )
    payload = json.loads(formatter.format(record))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test"
    assert payload["message"] == "hello"
    assert "timestamp" in payload


def test_json_log_formatter_redacts_sensitive_values():
    formatter = JsonLogFormatter()
    set_redaction_config(RedactionConfig(enabled=True, prefix=0, suffix=4))
    try:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=12,
            msg="channel=%s",
            args=(sensitive("123456789012345678"),),
            exc_info=None,
        )
        payload = json.loads(formatter.format(record))
    finally:
        set_redaction_config(RedactionConfig())

    assert payload["message"] == "channel=***5678"


def test_json_log_formatter_includes_optional_structured_event():
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=12,
        msg="Summary",
        args=(),
        exc_info=None,
    )
    for key, value in structured_event(
        "cleanup.summary",
        scope="run",
        messages_delete=2,
    ).items():
        setattr(record, key, value)

    payload = json.loads(formatter.format(record))

    assert payload["event"] == "cleanup.summary"
    assert payload["data"] == {
        "scope": "run",
        "messages_delete": 2,
    }


def test_structured_event_rejects_private_field_names():
    with pytest.raises(ValueError, match="private identifiers"):
        structured_event(
            "cleanup.action",
            message_id="private-value",
        )
