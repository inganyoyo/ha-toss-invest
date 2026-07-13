"""Release-documentation and automation contracts."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import zipfile

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).parents[1]
NO_OP_OPTIONS = {
    "enable_krw_conversion",
    "gain_color",
    "loss_color",
    "neutral_color",
    "border_color",
    "glow_color",
    "include_monetary_alert_payloads",
}
SECRET_PATTERN = re.compile(
    rb"(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
    rb"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    rb"Bearer [A-Za-z0-9._-]{20,})"
)


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _yaml(relative: str) -> dict:
    return yaml.load(_text(relative), Loader=yaml.BaseLoader)


def test_release_documentation_set_exists() -> None:
    for relative in (
        "README.md",
        "LICENSE",
        "docs/configuration.md",
        "docs/privacy.md",
        "docs/recorder.md",
        "docs/troubleshooting.md",
    ):
        assert (ROOT / relative).is_file(), relative


def test_readme_documents_install_and_security_boundaries() -> None:
    readme = _text("README.md")
    for phrase in (
        "read-only",
        "client_secret",
        "Privacy mode",
        "not an authorization boundary",
        "No order",
        "Custom repositories",
        "configuration.md",
        "privacy.md",
        "recorder.md",
        "troubleshooting.md",
    ):
        assert phrase in readme


def test_configuration_documents_every_option_default_and_bound() -> None:
    text = _text("docs/configuration.md")
    expected = {
        "open_price_interval": ("30", "10–300"),
        "holdings_interval": ("300", "30–3600"),
        "closed_price_interval": ("600", "60–3600"),
        "reference_interval": ("1800", "300–21600"),
        "candle_lookback": ("252", "20–500"),
        "max_retries": ("3", "0–5"),
        "request_timeout": ("10", "5–60"),
        "alert_cooldown": ("3600", "60–86400"),
        "daily_move_threshold": ("unset", "0–100"),
        "total_return_threshold": ("unset", "-100–1000"),
        "portfolio_daily_threshold": ("unset", "-100–100"),
        "near_high_threshold": ("unset", "0–100"),
        "near_low_threshold": ("unset", "0–100"),
        "drawdown_threshold": ("unset", "0–100"),
        "volume_spike_threshold": ("unset", "0–1000"),
    }
    for option, values in expected.items():
        assert option in text
        assert all(value in text for value in values), option
    for option in (
        "enable_manual_refresh",
        "enable_buying_power",
        "enable_rankings",
        "stock_warning_alerts_enabled",
        "stale_data_alerts_enabled",
        "api_failure_alerts_enabled",
    ):
        assert option in text
    assert "Settings > Devices & services" in text
    assert "client_id" in text and "client_secret" in text and "account_seq" in text
    assert not {option for option in NO_OP_OPTIONS if f"`{option}`" in text}
    assert "FX normalization is always enabled" in text
    assert "Lovelace theme variables" in text


def test_operator_docs_cover_privacy_recorder_and_request_ids() -> None:
    privacy = _text("docs/privacy.md")
    assert "not an authorization boundary" in privacy
    assert "client_secret" in privacy and ".storage" in privacy
    assert "always omitted" in privacy
    assert not {option for option in NO_OP_OPTIONS if f"`{option}`" in privacy}

    recorder = _text("docs/recorder.md")
    assert "recorder:" in recorder and "exclude:" in recorder
    assert "daily_candles" in recorder and "rankings" in recorder

    troubleshooting = _text("docs/troubleshooting.md")
    assert "request ID" in troubleshooting
    assert "real API" in troubleshooting and "order" in troubleshooting


def test_dashboard_docs_describe_fail_safe_monetary_alerts() -> None:
    for relative in ("dashboards/README.md", "dashboards/toss-invest-enhanced.yaml"):
        text = _text(relative)
        assert "금액 경고" in text and "항상 생략" in text
        assert "개인정보 옵션이 허용" not in text


def test_hacs_metadata_declares_supported_home_assistant_version() -> None:
    metadata = json.loads(_text("hacs.json"))
    assert metadata["name"] == "Toss Invest"
    assert metadata["content_in_root"] is False
    assert metadata["render_readme"] is True
    assert metadata["homeassistant"] == "2026.7.2"
    assert metadata["zip_release"] is True
    assert metadata["filename"] == "toss_invest.zip"


def test_ci_workflows_have_expected_gates_and_permissions() -> None:
    workflows = {
        name: _yaml(f".github/workflows/{name}.yaml")
        for name in ("test", "validate", "compatibility", "release")
    }
    test_text = _text(".github/workflows/test.yaml")
    assert 'python-version: "3.14"' in test_text
    assert "homeassistant==2026.7.2" in test_text
    for command in (
        "pytest -q",
        "ruff check .",
        "ruff format --check .",
        "mypy custom_components/toss_invest",
    ):
        assert command in test_text

    validate_text = _text(".github/workflows/validate.yaml")
    assert "home-assistant/actions/hassfest@master" in validate_text
    assert "hacs/action@main" in validate_text
    assert "category: integration" in validate_text
    assert "ignore: brands" in validate_text
    assert "MUST remove" in validate_text

    compatibility_text = _text(".github/workflows/compatibility.yaml")
    assert "schedule:" in compatibility_text and "cron:" in compatibility_text
    assert "ghcr.io/home-assistant/home-assistant:stable" in compatibility_text
    for package in (
        "pytest==9.0.3",
        "pytest-asyncio==1.4.0",
        "pytest-homeassistant-custom-component==0.13.346",
        "aioresponses==0.7.9",
        "ruff==0.15.21",
        "mypy==2.3.0",
    ):
        assert package in compatibility_text
    assert "stable-ha-constraint.txt" in compatibility_text
    assert "--constraint" in compatibility_text
    assert "installed Home Assistant changed during test-tool setup" in compatibility_text

    release_text = _text(".github/workflows/release.yaml")
    assert 'tags: ["v*"]' in release_text
    assert "toss_invest.zip" in release_text
    assert "softprops/action-gh-release@b4309332981a82ec1c5618f44dd2e27cc8bfbfda" in release_text
    assert "# v3.0.0" in release_text
    assert "contents: write" in release_text
    assert "manifest.json" in release_text
    assert "ignore: brands" in release_text
    assert "MUST remove" in release_text

    for name, workflow in workflows.items():
        assert workflow["permissions"] == {"contents": "read"}, name
        assert workflow.get("on") and workflow.get("jobs"), name
        for job_name, job in workflow["jobs"].items():
            assert job.get("runs-on") and isinstance(job.get("steps"), list), (name, job_name)
            expected = {"contents": "write"} if (name, job_name) == ("release", "publish") else None
            assert job.get("permissions") == expected, (name, job_name)
    assert workflows["release"]["jobs"]["publish"]["needs"] == ["quality", "validate"]

    workflow_text = "".join(_text(f".github/workflows/{name}.yaml") for name in workflows)
    assert "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd" in workflow_text
    assert "# v6.0.2" in workflow_text
    assert "actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405" in test_text
    assert "# v6.2.0" in test_text


def test_dependabot_and_bug_report_metadata_are_valid() -> None:
    dependabot = _yaml(".github/dependabot.yml")
    ecosystems = {item["package-ecosystem"] for item in dependabot["updates"]}
    assert ecosystems == {"github-actions", "pip"}

    bug = _yaml(".github/ISSUE_TEMPLATE/bug_report.yml")
    assert bug["name"] and bug["description"]
    body_ids = {item.get("id") for item in bug["body"]}
    assert {"ha_version", "integration_version", "request_id", "logs"} <= body_ids
    assert "client_secret" in _text(".github/ISSUE_TEMPLATE/bug_report.yml")
    assert "Dependabot" in _text("README.md") and "weekly" in _text("README.md")


def test_mypy_uses_explicit_namespace_package_bases() -> None:
    config = tomllib.loads(_text("pyproject.toml"))
    assert config["tool"]["mypy"]["explicit_package_bases"] is True


def test_release_tag_validator_accepts_only_manifest_version() -> None:
    script = ROOT / ".github/scripts/validate_release_tag.py"
    manifest = ROOT / "custom_components/toss_invest/manifest.json"
    version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    accepted = subprocess.run(
        [sys.executable, script, f"v{version}", manifest],
        check=False,
        capture_output=True,
        text=True,
    )
    rejected = subprocess.run(
        [sys.executable, script, "v9.9.9", manifest],
        check=False,
        capture_output=True,
        text=True,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert rejected.returncode != 0
    assert f"does not match manifest version v{version}" in rejected.stderr


def test_release_archive_has_hacs_root_layout_and_no_secrets(tmp_path: Path) -> None:
    archive = tmp_path / "toss_invest.zip"
    subprocess.run(
        [
            sys.executable,
            ROOT / ".github/scripts/build_release.py",
            ROOT / "custom_components/toss_invest",
            archive,
        ],
        check=True,
    )
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert {"__init__.py", "manifest.json", "config_flow.py"} <= names
        assert not any(name.startswith(("custom_components/", "toss_invest/")) for name in names)
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
        for name in names:
            assert not SECRET_PATTERN.search(bundle.read(name)), name
