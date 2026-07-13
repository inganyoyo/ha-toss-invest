import json
import re
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path("tests/fixtures")
FORBIDDEN_KEY_PATTERN = re.compile(r"client_secret|access_token|accountSeq")
FIXTURE_PATHS = sorted(FIXTURES_DIR.glob("*.json"))


def _iter_keys(value: Any) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _iter_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_keys(item)


def test_fixtures_directory_is_not_empty() -> None:
    assert FIXTURE_PATHS


@pytest.mark.parametrize("fixture_path", FIXTURE_PATHS, ids=lambda path: path.name)
def test_fixture_has_no_sensitive_keys(fixture_path: Path) -> None:
    data = json.loads(fixture_path.read_text())
    for key in _iter_keys(data):
        assert not FORBIDDEN_KEY_PATTERN.search(key), f"{fixture_path}: forbidden key {key!r}"


@pytest.mark.parametrize("fixture_path", FIXTURE_PATHS, ids=lambda path: path.name)
def test_fixture_raw_text_has_no_sensitive_patterns(fixture_path: Path) -> None:
    text = fixture_path.read_text()
    assert not FORBIDDEN_KEY_PATTERN.search(text), f"{fixture_path}: forbidden pattern present"
