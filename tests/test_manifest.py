import json
from pathlib import Path


def test_manifest_is_read_only_and_config_flow_enabled() -> None:
    manifest = json.loads(Path("custom_components/toss_invest/manifest.json").read_text())
    assert manifest["domain"] == "toss_invest"
    assert manifest["config_flow"] is True
    assert "iot_class" in manifest
    assert not any("order" in item for item in manifest.get("requirements", []))
