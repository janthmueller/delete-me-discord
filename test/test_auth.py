import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.auth import (
    KEYRING_SERVICE,
    AuthConfig,
    KeyringTokenStore,
    resolve_token,
    run_auth_command,
)


class FakeKeyringError(Exception):
    pass


class FakeKeyring:
    def __init__(self):
        self.values = {}
        self.fail_set = False

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        if self.fail_set:
            raise FakeKeyringError("set failed")
        self.values[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.values:
            raise FakeKeyringError("missing password")
        del self.values[(service, username)]


def _fake_keyring(monkeypatch):
    keyring = FakeKeyring()
    monkeypatch.setattr("delete_me_discord.auth._get_keyring", lambda: (keyring, FakeKeyringError))
    return keyring


def test_auth_config_roundtrip(tmp_path):
    config = AuthConfig(str(tmp_path / "config.json"))
    assert config.get_token() is None

    config.save_legacy_token("secret-token")
    assert config.get_token() == "secret-token"
    assert config.clear() is True
    assert config.get_token() is None


def test_auth_config_reads_and_clears_top_level_legacy_token(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"token": "legacy-token", "profiles": {"default": {"keep_last": 0}}}),
        encoding="utf-8",
    )
    config = AuthConfig(str(config_path))

    assert config.get_token() == "legacy-token"
    assert config.clear_token() is True

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data == {"profiles": {"default": {"keep_last": 0}}}


def test_auth_config_clears_nested_and_top_level_legacy_tokens(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"token": "top-level-token", "auth": {"token": "nested-token"}, "profiles": {"default": {}}}),
        encoding="utf-8",
    )
    config = AuthConfig(str(config_path))

    assert config.clear_token() is True

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data == {"profiles": {"default": {}}}


def test_keyring_token_store_roundtrip(tmp_path, monkeypatch):
    keyring = _fake_keyring(monkeypatch)
    config_path = tmp_path / "config.json"
    store = KeyringTokenStore(str(config_path))

    assert store.get_token() is None
    store.save_token("secret-token")

    assert store.get_token() == "secret-token"
    assert keyring.get_password(KEYRING_SERVICE, store.username) == "secret-token"
    assert store.clear_token() is True
    assert store.get_token() is None


def test_keyring_token_store_scopes_token_by_config_path(tmp_path, monkeypatch):
    _fake_keyring(monkeypatch)
    first = KeyringTokenStore(str(tmp_path / "first.json"))
    second = KeyringTokenStore(str(tmp_path / "second.json"))

    first.save_token("first-token")
    second.save_token("second-token")

    assert first.get_token() == "first-token"
    assert second.get_token() == "second-token"


def test_keyring_token_store_returns_none_when_keyring_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "delete_me_discord.auth._get_keyring",
        lambda: (_ for _ in ()).throw(RuntimeError("System keyring support is not installed.")),
    )

    assert KeyringTokenStore(str(tmp_path / "config.json")).get_token() is None
    assert KeyringTokenStore(str(tmp_path / "config.json")).clear_token() is False


def test_auth_config_load_rejects_non_object_root(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('["bad-root"]', encoding="utf-8")

    with pytest.raises(ValueError, match="Config root must be a JSON object"):
        AuthConfig(str(config_path)).load()


def test_auth_config_clear_returns_false_when_no_token_present(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"profiles": {"default": {}}}), encoding="utf-8")

    assert AuthConfig(str(config_path)).clear() is False


def test_resolve_token_prefers_argument_over_stored_tokens_and_env(tmp_path, monkeypatch):
    keyring = _fake_keyring(monkeypatch)
    config_path = tmp_path / "config.json"
    AuthConfig(str(config_path)).save_legacy_token("config-token")
    store = KeyringTokenStore(str(config_path))
    keyring.set_password(KEYRING_SERVICE, store.username, "keyring-token")
    monkeypatch.setenv("DISCORD_TOKEN", "env-token")

    token, source = resolve_token("arg-token", str(config_path))
    assert (token, source) == ("arg-token", "argument")


def test_resolve_token_prefers_keyring_over_legacy_config_and_env(tmp_path, monkeypatch):
    keyring = _fake_keyring(monkeypatch)
    config_path = tmp_path / "config.json"
    AuthConfig(str(config_path)).save_legacy_token("config-token")
    store = KeyringTokenStore(str(config_path))
    keyring.set_password(KEYRING_SERVICE, store.username, "keyring-token")
    monkeypatch.setenv("DISCORD_TOKEN", "env-token")

    token, source = resolve_token(None, str(config_path))
    assert (token, source) == ("keyring-token", "keyring")


def test_resolve_token_uses_legacy_config_when_keyring_empty(tmp_path, monkeypatch, caplog):
    _fake_keyring(monkeypatch)
    config_path = tmp_path / "config.json"
    AuthConfig(str(config_path)).save_legacy_token("config-token")
    monkeypatch.setenv("DISCORD_TOKEN", "env-token")

    token, source = resolve_token(None, str(config_path))
    assert (token, source) == ("config-token", "legacy config")
    assert "legacy plaintext config" in caplog.text


def test_resolve_token_uses_env_when_stored_tokens_missing(tmp_path, monkeypatch):
    _fake_keyring(monkeypatch)
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("DISCORD_TOKEN", "env-token")

    token, source = resolve_token(None, str(config_path))
    assert (token, source) == ("env-token", "environment")


def test_run_auth_login_migrates_nested_legacy_config_to_keyring(tmp_path, monkeypatch):
    keyring = _fake_keyring(monkeypatch)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"auth": {"token": "legacy-token"}, "profiles": {"default": {"keep_last": 0}}}),
        encoding="utf-8",
    )
    args = SimpleNamespace(command="login", replace=False, config_path=str(config_path))
    monkeypatch.setattr("delete_me_discord.auth.getpass.getpass", lambda *_: (_ for _ in ()).throw(AssertionError("should not prompt")))

    class FakeAPI:
        def __init__(self, token, **kwargs):
            assert token == "legacy-token"

        def get_current_user(self):
            return {"id": "123456789012345678", "username": "example-user"}

    monkeypatch.setattr("delete_me_discord.auth.DiscordAPI", FakeAPI)

    run_auth_command(args)
    store = KeyringTokenStore(str(config_path))
    assert keyring.get_password(KEYRING_SERVICE, store.username) == "legacy-token"
    assert AuthConfig(str(config_path)).get_token() is None
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data == {"profiles": {"default": {"keep_last": 0}}}


def test_run_auth_login_migrates_top_level_legacy_config_to_keyring(tmp_path, monkeypatch):
    keyring = _fake_keyring(monkeypatch)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"token": "legacy-token", "profiles": {"default": {"keep_last": 0}}}),
        encoding="utf-8",
    )
    args = SimpleNamespace(command="login", replace=False, config_path=str(config_path))
    monkeypatch.setattr("delete_me_discord.auth.getpass.getpass", lambda *_: (_ for _ in ()).throw(AssertionError("should not prompt")))

    class FakeAPI:
        def __init__(self, token, **kwargs):
            assert token == "legacy-token"

        def get_current_user(self):
            return {"id": "123456789012345678", "username": "example-user"}

    monkeypatch.setattr("delete_me_discord.auth.DiscordAPI", FakeAPI)

    run_auth_command(args)
    store = KeyringTokenStore(str(config_path))
    assert keyring.get_password(KEYRING_SERVICE, store.username) == "legacy-token"
    assert AuthConfig(str(config_path)).get_token() is None
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data == {"profiles": {"default": {"keep_last": 0}}}


def test_run_auth_login_uses_existing_keyring_token_and_cleans_legacy_config(tmp_path, monkeypatch):
    keyring = _fake_keyring(monkeypatch)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"token": "legacy-token", "profiles": {"default": {"keep_last": 0}}}),
        encoding="utf-8",
    )
    store = KeyringTokenStore(str(config_path))
    keyring.set_password(KEYRING_SERVICE, store.username, "keyring-token")
    args = SimpleNamespace(command="login", replace=False, config_path=str(config_path))
    monkeypatch.setattr("delete_me_discord.auth.getpass.getpass", lambda *_: (_ for _ in ()).throw(AssertionError("should not prompt")))

    class FakeAPI:
        def __init__(self, token, **kwargs):
            assert token == "keyring-token"

        def get_current_user(self):
            return {"id": "123456789012345678", "username": "example-user"}

    monkeypatch.setattr("delete_me_discord.auth.DiscordAPI", FakeAPI)

    run_auth_command(args)
    assert keyring.get_password(KEYRING_SERVICE, store.username) == "keyring-token"
    assert AuthConfig(str(config_path)).get_token() is None
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data == {"profiles": {"default": {"keep_last": 0}}}


def test_run_auth_login_does_not_rewrite_existing_keyring_token(tmp_path, monkeypatch):
    keyring = _fake_keyring(monkeypatch)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"token": "legacy-token", "profiles": {"default": {"keep_last": 0}}}),
        encoding="utf-8",
    )
    store = KeyringTokenStore(str(config_path))
    keyring.set_password(KEYRING_SERVICE, store.username, "keyring-token")
    keyring.fail_set = True
    args = SimpleNamespace(command="login", replace=False, config_path=str(config_path))
    monkeypatch.setattr("delete_me_discord.auth.getpass.getpass", lambda *_: (_ for _ in ()).throw(AssertionError("should not prompt")))

    class FakeAPI:
        def __init__(self, token, **kwargs):
            assert token == "keyring-token"

        def get_current_user(self):
            return {"id": "123456789012345678", "username": "example-user"}

    monkeypatch.setattr("delete_me_discord.auth.DiscordAPI", FakeAPI)

    run_auth_command(args)
    assert keyring.get_password(KEYRING_SERVICE, store.username) == "keyring-token"
    assert AuthConfig(str(config_path)).get_token() is None


def test_run_auth_login_prompts_when_no_stored_token_exists(tmp_path, monkeypatch):
    keyring = _fake_keyring(monkeypatch)
    config_path = tmp_path / "config.json"
    args = SimpleNamespace(command="login", replace=False, config_path=str(config_path))
    monkeypatch.setattr("delete_me_discord.auth.getpass.getpass", lambda *_: "prompt-token")

    class FakeAPI:
        def __init__(self, token, **kwargs):
            assert token == "prompt-token"

        def get_current_user(self):
            return {"id": "123456789012345678", "username": "example-user"}

    monkeypatch.setattr("delete_me_discord.auth.DiscordAPI", FakeAPI)

    run_auth_command(args)
    store = KeyringTokenStore(str(config_path))
    assert keyring.get_password(KEYRING_SERVICE, store.username) == "prompt-token"


def test_run_auth_login_passes_retry_options_to_api(tmp_path, monkeypatch):
    _fake_keyring(monkeypatch)
    config_path = tmp_path / "config.json"
    args = SimpleNamespace(
        command="login",
        replace=False,
        config_path=str(config_path),
        max_retries=7,
        retry_time_buffer=["2", "3"],
    )
    monkeypatch.setattr("delete_me_discord.auth.getpass.getpass", lambda *_: "prompt-token")

    class FakeAPI:
        def __init__(self, token, **kwargs):
            assert token == "prompt-token"
            assert kwargs["max_retries"] == 7
            assert kwargs["retry_time_buffer"] == (2.0, 3.0)

        def get_current_user(self):
            return {"id": "123456789012345678", "username": "example-user"}

    monkeypatch.setattr("delete_me_discord.auth.DiscordAPI", FakeAPI)

    run_auth_command(args)


def test_run_auth_login_replace_prompts_even_when_keyring_token_exists(tmp_path, monkeypatch):
    keyring = _fake_keyring(monkeypatch)
    config_path = tmp_path / "config.json"
    store = KeyringTokenStore(str(config_path))
    keyring.set_password(KEYRING_SERVICE, store.username, "old-token")
    args = SimpleNamespace(command="login", replace=True, config_path=str(config_path))
    monkeypatch.setattr("delete_me_discord.auth.getpass.getpass", lambda *_: "new-token")

    class FakeAPI:
        def __init__(self, token, **kwargs):
            assert token == "new-token"

        def get_current_user(self):
            return {"id": "123456789012345678", "username": "example-user"}

    monkeypatch.setattr("delete_me_discord.auth.DiscordAPI", FakeAPI)

    run_auth_command(args)
    assert keyring.get_password(KEYRING_SERVICE, store.username) == "new-token"


def test_run_auth_login_replace_removes_legacy_config_after_keyring_save(tmp_path, monkeypatch):
    keyring = _fake_keyring(monkeypatch)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"token": "legacy-token", "profiles": {"default": {"keep_last": 0}}}),
        encoding="utf-8",
    )
    args = SimpleNamespace(command="login", replace=True, config_path=str(config_path))
    monkeypatch.setattr("delete_me_discord.auth.getpass.getpass", lambda *_: "new-token")

    class FakeAPI:
        def __init__(self, token, **kwargs):
            assert token == "new-token"

        def get_current_user(self):
            return {"id": "123456789012345678", "username": "example-user"}

    monkeypatch.setattr("delete_me_discord.auth.DiscordAPI", FakeAPI)

    run_auth_command(args)
    store = KeyringTokenStore(str(config_path))
    assert keyring.get_password(KEYRING_SERVICE, store.username) == "new-token"
    assert AuthConfig(str(config_path)).get_token() is None
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data == {"profiles": {"default": {"keep_last": 0}}}


def test_run_auth_login_preserves_legacy_config_when_keyring_save_fails(tmp_path, monkeypatch):
    keyring = _fake_keyring(monkeypatch)
    keyring.fail_set = True
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"token": "legacy-token", "profiles": {"default": {"keep_last": 0}}}),
        encoding="utf-8",
    )
    args = SimpleNamespace(command="login", replace=False, config_path=str(config_path))
    monkeypatch.setattr("delete_me_discord.auth.getpass.getpass", lambda *_: (_ for _ in ()).throw(AssertionError("should not prompt")))

    class FakeAPI:
        def __init__(self, token, **kwargs):
            assert token == "legacy-token"

        def get_current_user(self):
            return {"id": "123456789012345678", "username": "example-user"}

    monkeypatch.setattr("delete_me_discord.auth.DiscordAPI", FakeAPI)

    with pytest.raises(SystemExit) as exc:
        run_auth_command(args)

    assert exc.value.code == 1
    assert AuthConfig(str(config_path)).get_token() == "legacy-token"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data == {"token": "legacy-token", "profiles": {"default": {"keep_last": 0}}}


def test_run_auth_login_exits_when_keyring_unavailable(tmp_path, monkeypatch):
    args = SimpleNamespace(command="login", replace=False, config_path=str(tmp_path / "config.json"))
    monkeypatch.setattr(
        "delete_me_discord.auth._get_keyring",
        lambda: (_ for _ in ()).throw(RuntimeError("System keyring support is not installed.")),
    )
    monkeypatch.setattr("delete_me_discord.auth.getpass.getpass", lambda *_: "prompt-token")

    class FakeAPI:
        def __init__(self, token, **kwargs):
            assert token == "prompt-token"

        def get_current_user(self):
            return {"id": "123456789012345678", "username": "example-user"}

    monkeypatch.setattr("delete_me_discord.auth.DiscordAPI", FakeAPI)

    with pytest.raises(SystemExit) as exc:
        run_auth_command(args)
    assert exc.value.code == 1


def test_run_auth_logout_removes_keyring_and_legacy_config(tmp_path, monkeypatch):
    keyring = _fake_keyring(monkeypatch)
    config_path = tmp_path / "config.json"
    AuthConfig(str(config_path)).save_legacy_token("legacy-token")
    store = KeyringTokenStore(str(config_path))
    keyring.set_password(KEYRING_SERVICE, store.username, "prompt-token")
    args = SimpleNamespace(command="logout", token=None, config_path=str(config_path))

    run_auth_command(args)
    assert keyring.get_password(KEYRING_SERVICE, store.username) is None
    assert not config_path.exists()


def test_run_auth_logout_preserves_non_auth_config(tmp_path, monkeypatch):
    _fake_keyring(monkeypatch)
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


def test_run_auth_login_exits_when_prompt_returns_empty(tmp_path, monkeypatch):
    _fake_keyring(monkeypatch)
    args = SimpleNamespace(command="login", replace=False, config_path=str(tmp_path / "config.json"))
    monkeypatch.setattr("delete_me_discord.auth.getpass.getpass", lambda *_: "   ")

    with pytest.raises(SystemExit) as exc:
        run_auth_command(args)
    assert exc.value.code == 1


def test_run_auth_login_exits_on_authentication_failure(tmp_path, monkeypatch):
    _fake_keyring(monkeypatch)
    args = SimpleNamespace(command="login", replace=False, config_path=str(tmp_path / "config.json"))
    monkeypatch.setattr("delete_me_discord.auth.getpass.getpass", lambda *_: "bad-token")

    class FakeAPI:
        def __init__(self, token, **kwargs):
            assert token == "bad-token"

        def get_current_user(self):
            raise delete_me_discord.auth.AuthenticationError("bad token")

    import delete_me_discord.auth

    monkeypatch.setattr("delete_me_discord.auth.DiscordAPI", FakeAPI)

    with pytest.raises(SystemExit) as exc:
        run_auth_command(args)
    assert exc.value.code == 1


def test_run_auth_whoami_requires_token(tmp_path, monkeypatch):
    _fake_keyring(monkeypatch)
    args = SimpleNamespace(command="whoami", token=None, config_path=str(tmp_path / "config.json"))

    with pytest.raises(SystemExit) as exc:
        run_auth_command(args)
    assert exc.value.code == 1


def test_run_auth_whoami_exits_on_authentication_failure(tmp_path, monkeypatch):
    args = SimpleNamespace(command="whoami", token="bad-token", config_path=str(tmp_path / "config.json"))

    class FakeAPI:
        def __init__(self, token, **kwargs):
            assert token == "bad-token"

        def get_current_user(self):
            raise delete_me_discord.auth.AuthenticationError("bad token")

    import delete_me_discord.auth

    monkeypatch.setattr("delete_me_discord.auth.DiscordAPI", FakeAPI)

    with pytest.raises(SystemExit) as exc:
        run_auth_command(args)
    assert exc.value.code == 1


def test_run_auth_command_rejects_unknown_command(tmp_path):
    args = SimpleNamespace(command="wat", token=None, config_path=str(tmp_path / "config.json"))

    with pytest.raises(ValueError, match="Unsupported auth command"):
        run_auth_command(args)
