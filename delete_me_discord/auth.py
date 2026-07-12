import getpass
import json
import logging
import os
from typing import Optional, Tuple

from .api import DiscordAPI
from .privacy import sensitive, sensitive_name
from .storage import atomic_write_json
from .utils import AuthenticationError, parse_random_range


DEFAULT_CONFIG_PATH = os.path.join(
    os.path.expanduser("~"),
    ".config",
    "delete-me-discord",
    "config.json",
)
KEYRING_SERVICE = "delete-me-discord"


class AuthConfig:
    """Minimal JSON config helper with legacy token compatibility."""

    def __init__(self, path: str = DEFAULT_CONFIG_PATH):
        self.path = path or DEFAULT_CONFIG_PATH

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
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
        self.username = f"token:{os.path.abspath(os.path.expanduser(self.config_path))}"

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


def resolve_token(token_arg: Optional[str], config_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Resolve token by priority: CLI arg, environment, keyring, legacy config file."""
    if token_arg:
        return token_arg, "argument"

    token = os.getenv("DISCORD_TOKEN")
    if token:
        return token, "environment"

    token = KeyringTokenStore(config_path).get_token()
    if token:
        return token, "keyring"

    config = AuthConfig(config_path)
    token = config.get_token()
    if token:
        logging.getLogger("auth").warning(
            "Token is stored in legacy plaintext config. Run `dmd login` to move it to the system keyring."
        )
        return token, "legacy config"

    return None, None


def run_auth_command(args) -> None:
    logger = logging.getLogger("auth")
    config = AuthConfig(args.config_path)
    keyring_store = KeyringTokenStore(args.config_path)

    if args.command == "logout":
        keyring_deleted = keyring_store.clear_token()
        legacy_deleted = config.clear_token()
        deleted = keyring_deleted or legacy_deleted
        if deleted:
            logger.info("Removed stored token.")
        else:
            logger.info("No stored token found.")
        return

    if args.command == "login":
        replace = getattr(args, "replace", False)
        token_source = "prompt"
        token = None
        if not replace:
            token = keyring_store.get_token()
            if token:
                token_source = "keyring"
            else:
                token = config.get_token()
                if token:
                    token_source = "legacy config"
        if not token:
            token = getpass.getpass("Discord token: ").strip()
            token_source = "prompt"
        if not token:
            logger.error("No token provided.")
            raise SystemExit(1)

        current_user = _validate_token(token, args=args)

        if token_source != "keyring":
            try:
                keyring_store.save_token(token)
            except RuntimeError as exc:
                logger.error("%s Use DISCORD_TOKEN or a command-specific --token override instead.", exc)
                raise SystemExit(1)
        legacy_deleted = config.clear_token()
        username = sensitive_name(current_user.get("username", "unknown"))
        user_id = sensitive(current_user.get("id", "unknown"))
        if token_source == "keyring":
            if legacy_deleted:
                logger.info(
                    "Already logged in as %s (%s) using the system keyring. Removed legacy plaintext token from config.",
                    username,
                    user_id,
                )
            else:
                logger.info(
                    "Already logged in as %s (%s) using the system keyring.",
                    username,
                    user_id,
                )
            return
        if token_source == "legacy config":
            logger.info(
                "Migrated token for %s (%s) from legacy config to the system keyring.",
                username,
                user_id,
            )
            return
        logger.info(
            "Stored token for %s (%s) in the system keyring.",
            username,
            user_id,
        )
        return

    if args.command == "whoami":
        token, source = resolve_token(args.token, args.config_path)
        if not token:
            logger.error("Discord token not provided. Run dmd login, set DISCORD_TOKEN, or use --token.")
            raise SystemExit(1)

        try:
            api = _build_auth_api(token, args)
            current_user = api.get_current_user()
        except AuthenticationError as exc:
            logger.error("Authentication failed (invalid token?): %s", exc)
            raise SystemExit(1)

        logger.info(
            "Authenticated as %s (%s) using token from %s.",
            sensitive_name(current_user.get("username", "unknown")),
            sensitive(current_user.get("id", "unknown")),
            source,
        )
        return

    raise ValueError(f"Unsupported auth command: {args.command}")


def _validate_token(token: str, *, args=None) -> dict:
    try:
        api = _build_auth_api(token, args)
        return api.get_current_user()
    except AuthenticationError as exc:
        logging.getLogger("auth").error("Authentication failed (invalid token?): %s", exc)
        raise SystemExit(1) from exc


def _build_auth_api(token: str, args=None) -> DiscordAPI:
    retry_time_buffer = getattr(args, "retry_time_buffer", ["0.1", "0.3"])
    return DiscordAPI(
        token=token,
        max_retries=getattr(args, "max_retries", 5),
        retry_time_buffer=parse_random_range(retry_time_buffer, "retry-time-buffer"),
        request_intervals=getattr(args, "request_intervals", {}),
    )
