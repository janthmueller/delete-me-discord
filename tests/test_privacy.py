# delete-me-discord privacy tests
import sys
from pathlib import Path

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.privacy import RedactionConfig, sensitive, sensitive_name, set_redaction_config


def test_sensitive_value_get_sensitive_value_returns_raw_string():
    wrapped = sensitive("123456789012345678")
    assert wrapped.get_sensitive_value() == "123456789012345678"


def test_sensitive_value_full_mask_when_enabled_without_window():
    set_redaction_config(RedactionConfig(enabled=True, prefix=0, suffix=0))
    try:
        assert str(sensitive("123456789012345678")) == "***"
    finally:
        set_redaction_config(RedactionConfig())


def test_sensitive_value_masks_when_window_exceeds_string_length():
    set_redaction_config(RedactionConfig(enabled=True, prefix=3, suffix=3))
    try:
        assert str(sensitive("12345")) == "***"
    finally:
        set_redaction_config(RedactionConfig())


def test_sensitive_name_can_remain_visible_when_name_redaction_disabled():
    set_redaction_config(RedactionConfig(enabled=True, prefix=0, suffix=4, redact_names=False))
    try:
        assert str(sensitive_name("general")) == "general"
        assert str(sensitive("message content", full=True)) == "***"
        assert str(sensitive("123456789012345678")) == "***5678"
    finally:
        set_redaction_config(RedactionConfig())
