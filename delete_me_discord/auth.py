import getpass
import json
import logging
import os
from typing import Optional, Tuple

from .api import DiscordAPI
from .privacy import sensitive
from .utils import AuthenticationError


DEFAULT_AUTH_CONFIG_PATH = os.path.join(
    os.path.expanduser("~"),
    ".config",
    "delete-me-discord",
    "config.json",
)


class AuthConfig:
    """Minimal config store for the Discord user token."""

    def __init__(self, path: str = DEFAULT_AUTH_CONFIG_PATH):
        self.path = path or DEFAULT_AUTH_CONFIG_PATH

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return {}
        if not isinstance(raw, dict):
            raise ValueError("Auth config root must be a JSON object.")
        return raw

    def get_token(self) -> Optional[str]:
        raw = self.load()
        auth = raw.get("auth")
        if not isinstance(auth, dict):
            return None
        token = auth.get("token")
        return token if isinstance(token, str) and token.strip() else None

    def save_token(self, token: str) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = self.load()
        auth = payload.get("auth")
        if not isinstance(auth, dict):
            auth = {}
        auth["token"] = token
        payload["auth"] = auth
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)

    def clear(self) -> bool:
        payload = self.load()
        auth = payload.get("auth")
        if not isinstance(auth, dict) or "token" not in auth:
            return False
        del auth["token"]
        if auth:
            payload["auth"] = auth
        else:
            payload.pop("auth", None)
        if payload:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
        elif os.path.exists(self.path):
            os.remove(self.path)
        return True


def resolve_token(token_arg: Optional[str], config_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Resolve token by priority: CLI arg, config file, environment variable."""
    if token_arg:
        return token_arg, "argument"

    config = AuthConfig(config_path)
    token = config.get_token()
    if token:
        return token, "config"

    token = os.getenv("DISCORD_TOKEN")
    if token:
        return token, "environment"

    return None, None


def run_auth_command(args) -> None:
    logger = logging.getLogger("auth")
    config = AuthConfig(args.auth_config_path)

    if args.command == "logout":
        deleted = config.clear()
        if deleted:
            logger.info("Removed stored token from %s.", sensitive(config.path, full=True))
        else:
            logger.info("No stored token found at %s.", sensitive(config.path, full=True))
        return

    if args.command == "login":
        token = args.token
        if not token:
            token = getpass.getpass("Discord token: ").strip()
        if not token:
            logger.error("No token provided.")
            raise SystemExit(1)

        try:
            api = DiscordAPI(token=token)
            current_user = api.get_current_user()
        except AuthenticationError as exc:
            logger.error("Authentication failed (invalid token?): %s", exc)
            raise SystemExit(1)

        config.save_token(token)
        logger.info(
            "Stored token for %s (%s) at %s.",
            sensitive(current_user.get("username", "unknown"), full=True),
            sensitive(current_user.get("id", "unknown")),
            sensitive(config.path, full=True),
        )
        return

    if args.command == "whoami":
        token, source = resolve_token(args.token, args.auth_config_path)
        if not token:
            logger.error("Discord token not provided. Use --token, dmd login, or set DISCORD_TOKEN.")
            raise SystemExit(1)

        try:
            api = DiscordAPI(token=token)
            current_user = api.get_current_user()
        except AuthenticationError as exc:
            logger.error("Authentication failed (invalid token?): %s", exc)
            raise SystemExit(1)

        logger.info(
            "Authenticated as %s (%s) using token from %s.",
            sensitive(current_user.get("username", "unknown"), full=True),
            sensitive(current_user.get("id", "unknown")),
            source,
        )
        return

    raise ValueError(f"Unsupported auth command: {args.command}")
