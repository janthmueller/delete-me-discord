from pathlib import Path
import tomllib


def test_project_scripts_include_short_alias():
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    scripts = data["project"]["scripts"]

    assert scripts["delete-me-discord"] == "delete_me_discord:main"
    assert scripts["dmd"] == "delete_me_discord:main"
