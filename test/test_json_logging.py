# delete-me-discord JSON logging tests
import json
import logging
import sys
from pathlib import Path

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.utils import JsonLogFormatter


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
