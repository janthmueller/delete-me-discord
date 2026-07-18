"""OS keyring persistence and legacy plaintext-token migration support."""

import json
import os
from typing import Optional

from ..config import DEFAULT_CONFIG_PATH
from ..storage import atomic_write_json


KEYRING_SERVICE = "delete-me-discord"


class AuthConfig:
    """Minimal JSON config helper with legacy token compatibility."""

    def __init__(self, path: str = DEFAULT_CONFIG_PATH):
        self.path = path or DEFAULT_CONFIG_PATH

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                raw = json.load(file)
        except FileNotFoundError:
            return {}
        if not isinstance(raw, dict):
            raise ValueError("Config root must be a JSON object.")
        return raw

    def get_token(self) -> Optional[str]:
        raw = self.load()
        token = raw.get("token")
        if isinstance(token, str) and token.strip():
            return token
        auth = raw.get("auth")
        if not isinstance(auth, dict):
            return None
        token = auth.get("token")
        return token if isinstance(token, str) and token.strip() else None

    def save_legacy_token(self, token: str) -> None:
        payload = self.load()
        auth = payload.get("auth")
        if not isinstance(auth, dict):
            auth = {}
        auth["token"] = token
        payload["auth"] = auth
        atomic_write_json(self.path, payload)

    def clear_token(self) -> bool:
        payload = self.load()
        removed = False
        if "token" in payload:
            del payload["token"]
            removed = True
        auth = payload.get("auth")
        if isinstance(auth, dict) and "token" in auth:
            del auth["token"]
            removed = True
        if not removed:
            return False
        if isinstance(auth, dict) and auth:
            payload["auth"] = auth
        else:
            payload.pop("auth", None)
        if payload:
            atomic_write_json(self.path, payload)
        elif os.path.exists(self.path):
            os.remove(self.path)
        return True

    def clear(self) -> bool:
        return self.clear_token()


def _get_keyring():
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:
        raise RuntimeError("System keyring support is not installed.") from exc
    return keyring, KeyringError


class KeyringTokenStore:
    """Store Discord tokens in the OS keyring, scoped by config path."""

    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self.username = (
            f"token:{os.path.abspath(os.path.expanduser(self.config_path))}"
        )

    def get_token(self) -> Optional[str]:
        try:
            keyring, KeyringError = _get_keyring()
        except RuntimeError:
            return None
        try:
            token = keyring.get_password(KEYRING_SERVICE, self.username)
        except KeyringError:
            return None
        return token if isinstance(token, str) and token.strip() else None

    def save_token(self, token: str) -> None:
        keyring, KeyringError = _get_keyring()
        try:
            keyring.set_password(KEYRING_SERVICE, self.username, token)
        except KeyringError as exc:
            raise RuntimeError("System keyring is unavailable.") from exc

    def clear_token(self) -> bool:
        try:
            keyring, KeyringError = _get_keyring()
        except RuntimeError:
            return False
        try:
            existing = keyring.get_password(KEYRING_SERVICE, self.username)
            if existing is None:
                return False
            keyring.delete_password(KEYRING_SERVICE, self.username)
        except KeyringError:
            return False
        return True
