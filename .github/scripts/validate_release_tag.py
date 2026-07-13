"""Require a release tag to match the integration manifest version."""

from __future__ import annotations

import json
from pathlib import Path
import sys


def validate_release_tag(tag: str, manifest_path: Path) -> None:
    """Raise when a tag is not exactly `v<manifest version>`."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("manifest version must be a non-empty string")
    expected = f"v{version}"
    if tag != expected:
        raise ValueError(f"tag {tag!r} does not match manifest version {expected}")


def main(argv: list[str]) -> int:
    """Validate command-line arguments and report a concise release error."""
    if len(argv) != 3:
        print(f"usage: {argv[0]} TAG MANIFEST", file=sys.stderr)
        return 2
    try:
        validate_release_tag(argv[1], Path(argv[2]))
    except (OSError, ValueError, json.JSONDecodeError) as err:
        print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
