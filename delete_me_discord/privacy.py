from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RedactionConfig:
    """Runtime redaction configuration for log-facing sensitive values."""

    enabled: bool = False
    prefix: int = 0
    suffix: int = 0
    mask: str = "***"

    def redact(self, value: object) -> str:
        """Render a value according to the active redaction settings."""
        text = str(value)
        if not self.enabled:
            return text

        if self.prefix <= 0 and self.suffix <= 0:
            return self.mask

        if self.prefix + self.suffix >= len(text):
            return self.mask

        start = text[: self.prefix] if self.prefix > 0 else ""
        end = text[-self.suffix :] if self.suffix > 0 else ""
        return f"{start}{self.mask}{end}"


_redaction_config = RedactionConfig()


def set_redaction_config(config: Optional[RedactionConfig]) -> None:
    """Replace the active global redaction configuration."""
    global _redaction_config
    _redaction_config = config or RedactionConfig()


def get_redaction_config() -> RedactionConfig:
    """Return the active global redaction configuration."""
    return _redaction_config


class SensitiveValue:
    """Wrapper whose string rendering respects the active redaction config."""

    def __init__(self, value: object, full: bool = False):
        self._value = value
        self._full = full

    def get_sensitive_value(self) -> str:
        """Return the unredacted underlying value as a string."""
        return str(self._value)

    def __str__(self) -> str:
        config = get_redaction_config()
        if not config.enabled:
            return str(self._value)
        if self._full:
            return config.mask
        return config.redact(self._value)

    def __repr__(self) -> str:
        return str(self)


def sensitive(value: object, full: bool = False) -> SensitiveValue:
    """Wrap a value so log formatting redacts it when enabled."""
    return SensitiveValue(value, full=full)
