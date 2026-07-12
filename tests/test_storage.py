import json
import os
import stat

import pytest

from delete_me_discord.storage import atomic_write_json


def test_atomic_write_json_replaces_content_and_sets_private_mode(tmp_path):
    path = tmp_path / "config.json"

    atomic_write_json(str(path), {"value": 1})
    atomic_write_json(str(path), {"value": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 2}
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_atomic_write_json_preserves_old_file_when_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"value": 1}\n', encoding="utf-8")

    monkeypatch.setattr("delete_me_discord.storage.os.replace", lambda *_: (_ for _ in ()).throw(OSError("full")))

    with pytest.raises(OSError, match="full"):
        atomic_write_json(str(path), {"value": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 1}
    assert list(tmp_path.glob(".config.json.*.tmp")) == []
