import sys
from pathlib import Path

from rich.table import Table

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.progress import CleanerProgress


def test_buffering_context_disabled_returns_noop_context():
    progress = CleanerProgress()

    with progress.buffering_context(enabled=False) as live:
        assert live is None


def test_buffering_context_enabled_constructs_live(monkeypatch):
    progress = CleanerProgress()
    captured = {}

    class FakeLive:
        def __init__(self, renderable, console, refresh_per_second, transient):
            captured["renderable"] = renderable
            captured["console"] = console
            captured["refresh_per_second"] = refresh_per_second
            captured["transient"] = transient

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("delete_me_discord.progress.Live", FakeLive)

    with progress.buffering_context(enabled=True) as live:
        assert isinstance(live, FakeLive)

    assert isinstance(captured["renderable"], Table)
    assert captured["refresh_per_second"] == 12
    assert captured["transient"] is True


def test_update_buffering_ignores_none():
    progress = CleanerProgress()

    progress.update_buffering(None, 5)


def test_update_buffering_updates_live_renderable(monkeypatch):
    progress = CleanerProgress()
    captured = {}

    class FakeLive:
        def update(self, renderable):
            captured["renderable"] = renderable

    fake_live = FakeLive()
    progress.update_buffering(fake_live, 7)

    assert isinstance(captured["renderable"], Table)


def test_render_buffering_status_returns_table():
    progress = CleanerProgress()

    renderable = progress.render_buffering_status(42)

    assert isinstance(renderable, Table)


def test_action_progress_disabled_returns_noop_context():
    progress = CleanerProgress()

    with progress.action_progress(enabled=False, total_actions=5, description="Actions") as state:
        assert state == (None, None)

    with progress.action_progress(enabled=True, total_actions=0, description="Actions") as state:
        assert state == (None, None)


def test_action_progress_enabled_starts_and_stops_progress(monkeypatch):
    progress = CleanerProgress()
    captured = {}

    class FakeProgress:
        def __init__(self, *columns, console, transient):
            captured["columns"] = columns
            captured["console"] = console
            captured["transient"] = transient
            self.started = False
            self.stopped = False

        def start(self):
            self.started = True

        def add_task(self, description, total):
            captured["description"] = description
            captured["total"] = total
            return "task-1"

        def stop(self):
            self.stopped = True

    monkeypatch.setattr("delete_me_discord.progress.Progress", FakeProgress)

    with progress.action_progress(enabled=True, total_actions=3, description="Actions") as state:
        fake_progress, task_id = state
        assert isinstance(fake_progress, FakeProgress)
        assert fake_progress.started is True
        assert task_id == "task-1"

    assert captured["description"] == "Actions"
    assert captured["total"] == 3
    assert captured["transient"] is True
    assert fake_progress.stopped is True
