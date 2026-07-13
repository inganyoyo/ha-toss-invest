"""Build a HACS integration archive with files at ZIP root."""

from __future__ import annotations

from pathlib import Path
import sys
import zipfile

_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def build_release(source: Path, output: Path) -> None:
    """Archive regular integration files without cache directories or path prefixes."""
    if not (source / "manifest.json").is_file() or not (source / "__init__.py").is_file():
        raise ValueError(f"{source} is not a Home Assistant integration directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if (
                not path.is_file()
                or "__pycache__" in relative.parts
                or path.suffix in _EXCLUDED_SUFFIXES
            ):
                continue
            archive.write(path, relative.as_posix())


def main(argv: list[str]) -> int:
    """Build an archive from command-line paths."""
    if len(argv) != 3:
        print(f"usage: {argv[0]} SOURCE OUTPUT", file=sys.stderr)
        return 2
    try:
        build_release(Path(argv[1]), Path(argv[2]))
    except (OSError, ValueError, zipfile.BadZipFile) as err:
        print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
