from __future__ import annotations

import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import subprocess

import pytest


ROOT = Path(__file__).parents[1]
COMPOSE = ROOT / "dev" / "compose.yaml"


def _normalized_compose(
    *,
    secrets_source: str | None = None,
    cwd: Path = ROOT,
) -> dict[str, object]:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is not installed")

    environment = os.environ.copy()
    environment.pop("TOSS_INVEST_DEV_SECRETS", None)
    if secrets_source is not None:
        environment["TOSS_INVEST_DEV_SECRETS"] = secrets_source

    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE.relative_to(cwd)), "config", "--format", "json"],
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    document = json.loads(result.stdout)
    assert isinstance(document, dict)
    return document


def _home_assistant_volumes(document: dict[str, object]) -> list[dict[str, object]]:
    services = document["services"]
    assert isinstance(services, dict)
    home_assistant = services["homeassistant"]
    assert isinstance(home_assistant, dict)
    volumes = home_assistant["volumes"]
    assert isinstance(volumes, list)
    return volumes


@pytest.mark.parametrize(
    ("override", "expected_source"),
    (
        (None, ROOT / "dev" / "secrets.yaml.example"),
        ("./secrets.yaml", ROOT / "dev" / "secrets.yaml"),
    ),
)
@pytest.mark.parametrize("cwd", (ROOT, ROOT / "dev"))
def test_compose_normalizes_default_and_override_secrets_without_target_overlap(
    override: str | None, expected_source: Path, cwd: Path
) -> None:
    volumes = _home_assistant_volumes(_normalized_compose(secrets_source=override, cwd=cwd))
    secrets = [volume for volume in volumes if volume["target"] == "/config/secrets.yaml"]

    assert len(secrets) == 1
    assert Path(str(secrets[0]["source"])) == expected_source
    assert secrets[0]["read_only"] is True
    targets = [str(volume["target"]) for volume in volumes]
    assert len(targets) == len(set(targets))
    assert "/config" not in targets
    paths = [PurePosixPath(target) for target in targets]
    assert not {
        (parent, child)
        for parent in paths
        for child in paths
        if parent != child and parent in child.parents
    }


def test_dev_compose_persists_only_home_assistant_storage() -> None:
    volumes = _home_assistant_volumes(_normalized_compose())
    storage = [volume for volume in volumes if volume["target"] == "/config/.storage"]

    assert len(storage) == 1
    assert Path(str(storage[0]["source"])) == ROOT / "dev" / "config" / ".storage"


def test_dev_compose_healthcheck_uses_image_python_and_local_home_assistant() -> None:
    document = _normalized_compose()
    services = document["services"]
    assert isinstance(services, dict)
    home_assistant = services["homeassistant"]
    assert isinstance(home_assistant, dict)
    healthcheck = home_assistant["healthcheck"]
    assert isinstance(healthcheck, dict)

    command = healthcheck["test"]
    assert isinstance(command, list)
    assert command[:3] == ["CMD", "python3", "-c"]
    assert "urllib.request" in str(command[3])
    assert "http://localhost:8123/" in str(command[3])
    assert healthcheck == {
        "test": command,
        "timeout": "5s",
        "interval": "10s",
        "retries": 6,
        "start_period": "30s",
    }


def test_dev_readme_documents_exact_secrets_override_invocation() -> None:
    readme = (ROOT / "dev" / "README.md").read_text(encoding="utf-8")

    assert (
        "TOSS_INVEST_DEV_SECRETS=./secrets.yaml docker compose -f dev/compose.yaml up -d" in readme
    )
