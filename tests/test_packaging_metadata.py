from pathlib import Path

from delete_me_discord._version import __version__

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


def test_project_scripts_include_short_alias():
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    scripts = data["project"]["scripts"]

    assert scripts["delete-me-discord"] == "delete_me_discord.cli.commands:main"
    assert scripts["dmd"] == "delete_me_discord.cli.commands:main"


def test_source_version_matches_project_metadata():
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert __version__ == data["project"]["version"]
    assert data["tool"]["semantic_release"]["version_variables"] == [
        "delete_me_discord/_version.py:__version__"
    ]
