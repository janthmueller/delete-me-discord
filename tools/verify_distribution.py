import argparse
import email
import tarfile
import zipfile
from pathlib import Path


def verify_wheel(path: Path, expected_version: str) -> None:
    dist_info_root = f"delete_me_discord-{expected_version}.dist-info/"
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        unexpected = sorted(
            name
            for name in names
            if not name.startswith(("delete_me_discord/", dist_info_root))
        )
        if unexpected:
            raise SystemExit(f"Unexpected wheel entries: {', '.join(unexpected)}")

        metadata_files = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            raise SystemExit(f"Expected one wheel METADATA file, found {len(metadata_files)}")
        metadata = email.message_from_bytes(wheel.read(metadata_files[0]))
        if metadata["Version"] != expected_version:
            raise SystemExit(
                f"Wheel version {metadata['Version']!r} does not match {expected_version!r}"
            )


def verify_sdist(path: Path, expected_version: str) -> None:
    root = f"delete_me_discord-{expected_version}/"
    allowed_roots = (
        f"{root}delete_me_discord/",
        f"{root}delete_me_discord.egg-info/",
    )
    allowed_files = {
        f"{root}LICENSE",
        f"{root}MANIFEST.in",
        f"{root}PKG-INFO",
        f"{root}README.md",
        f"{root}pyproject.toml",
        f"{root}setup.cfg",
        f"{root}setup.py",
    }
    with tarfile.open(path, "r:gz") as sdist:
        names = [member.name for member in sdist.getmembers() if member.isfile()]
    unexpected = sorted(
        name
        for name in names
        if name not in allowed_files and not name.startswith(allowed_roots)
    )
    if unexpected:
        raise SystemExit(f"Unexpected sdist entries: {', '.join(unexpected)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate built Python distributions.")
    parser.add_argument("wheel", type=Path)
    parser.add_argument("sdist", type=Path)
    parser.add_argument("expected_version")
    args = parser.parse_args()
    verify_wheel(args.wheel, args.expected_version)
    verify_sdist(args.sdist, args.expected_version)


if __name__ == "__main__":
    main()
