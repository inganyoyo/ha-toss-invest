"""Regression tests for the disposable Home Assistant development environment."""

from pathlib import Path, PurePosixPath

import yaml  # type: ignore[import-untyped]


def test_dev_compose_mount_targets_do_not_overlap() -> None:
    """Docker Desktop must not receive parent and child bind mount targets."""
    compose = yaml.safe_load(Path("dev/compose.yaml").read_text())
    volumes = compose["services"]["homeassistant"]["volumes"]
    targets = [PurePosixPath(volume.rsplit(":", 2)[1]) for volume in volumes]

    overlaps = {
        (str(parent), str(child))
        for parent in targets
        for child in targets
        if parent != child and parent in child.parents
    }

    assert not overlaps
    assert PurePosixPath("/config/.storage") in targets
