import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.auth import AuthConfig, resolve_token, run_auth_command


def test_auth_config_roundtrip(tmp_path):
    config = AuthConfig(str(tmp_path / "config.json"))
    assert config.get_token() is None

    config.save_token("secret-token")
    assert config.get_token() == "secret-token"
    assert config.clear() is True
    assert config.get_token() is None


def test_resolve_token_prefers_argument_over_config_and_env(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    AuthConfig(str(config_path)).save_token("config-token")
    monkeypatch.setenv("DISCORD_TOKEN", "env-token")

    token, source = resolve_token("arg-token", str(config_path))
    assert (token, source) == ("arg-token", "argument")


def test_resolve_token_prefers_config_over_env(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    AuthConfig(str(config_path)).save_token("config-token")
    monkeypatch.setenv("DISCORD_TOKEN", "env-token")

    token, source = resolve_token(None, str(config_path))
    assert (token, source) == ("config-token", "config")


def test_run_auth_login_reads_prompt_and_saves_token(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    args = SimpleNamespace(command="login", token=None, config_path=str(config_path))
    monkeypatch.setattr("delete_me_discord.auth.getpass.getpass", lambda *_: "prompt-token")

    class FakeAPI:
        def __init__(self, token, **kwargs):
            assert token == "prompt-token"

        def get_current_user(self):
            return {"id": "123456789012345678", "username": "example-user"}

    monkeypatch.setattr("delete_me_discord.auth.DiscordAPI", FakeAPI)

    run_auth_command(args)
    assert AuthConfig(str(config_path)).get_token() == "prompt-token"


def test_run_auth_logout_removes_config(tmp_path):
    config_path = tmp_path / "config.json"
    AuthConfig(str(config_path)).save_token("prompt-token")
    args = SimpleNamespace(command="logout", token=None, config_path=str(config_path))

    run_auth_command(args)
    assert not config_path.exists()


def test_run_auth_logout_preserves_non_auth_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "auth": {"token": "prompt-token"},
                "profiles": {"default": {"keep_last": 0}},
            }
        ),
        encoding="utf-8",
    )
    args = SimpleNamespace(command="logout", token=None, config_path=str(config_path))

    run_auth_command(args)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "auth" not in data
    assert data["profiles"]["default"]["keep_last"] == 0


def test_run_auth_whoami_requires_token(tmp_path):
    args = SimpleNamespace(command="whoami", token=None, config_path=str(tmp_path / "config.json"))

    with pytest.raises(SystemExit) as exc:
        run_auth_command(args)
    assert exc.value.code == 1
