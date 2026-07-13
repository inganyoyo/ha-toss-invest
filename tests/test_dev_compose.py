"""Regression tests for the disposable Home Assistant development environment."""

from pathlib import Path, PurePosixPath

import yaml  # type: ignore[import-untyped]


def _dev_mounts() -> dict[str, str]:
    """Return container targets mapped to their Compose bind sources."""
    compose = yaml.safe_load(Path("dev/compose.yaml").read_text())
    volumes = compose["services"]["homeassistant"]["volumes"]
    return {volume.split(":", 2)[1]: volume.split(":", 2)[0] for volume in volumes}


def test_dev_compose_mount_targets_do_not_overlap() -> None:
    """Docker Desktop must not receive parent and child bind mount targets."""
    targets = [PurePosixPath(target) for target in _dev_mounts()]

    overlaps = {
        (str(parent), str(child))
        for parent in targets
        for child in targets
        if parent != child and parent in child.parents
    }

    assert not overlaps


def test_dev_compose_persists_only_home_assistant_storage() -> None:
    """HA registry/auth state uses the matching host .storage directory."""
    assert _dev_mounts()["/config/.storage"] == "./config/.storage"
