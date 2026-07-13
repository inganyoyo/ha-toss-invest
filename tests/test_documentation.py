"""Release-documentation and automation contracts."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib

import yaml

ROOT = Path(__file__).parents[1]


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
        "enable_krw_conversion",
        "enable_buying_power",
        "enable_rankings",
        "gain_color",
        "loss_color",
        "neutral_color",
        "border_color",
        "glow_color",
        "include_monetary_alert_payloads",
        "stock_warning_alerts_enabled",
        "stale_data_alerts_enabled",
        "api_failure_alerts_enabled",
    ):
        assert option in text
    assert "Settings > Devices & services" in text
    assert "client_id" in text and "client_secret" in text and "account_seq" in text


def test_operator_docs_cover_privacy_recorder_and_request_ids() -> None:
    privacy = _text("docs/privacy.md")
    assert "not an authorization boundary" in privacy
    assert "client_secret" in privacy and ".storage" in privacy
    assert "include_monetary_alert_payloads" in privacy

    recorder = _text("docs/recorder.md")
    assert "recorder:" in recorder and "exclude:" in recorder
    assert "daily_candles" in recorder and "rankings" in recorder

    troubleshooting = _text("docs/troubleshooting.md")
    assert "request ID" in troubleshooting
    assert "real API" in troubleshooting and "order" in troubleshooting


def test_hacs_metadata_declares_supported_home_assistant_version() -> None:
    metadata = json.loads(_text("hacs.json"))
    assert metadata["name"] == "Toss Invest"
    assert metadata["content_in_root"] is False
    assert metadata["render_readme"] is True
    assert metadata["homeassistant"] == "2026.7.2"


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

    compatibility_text = _text(".github/workflows/compatibility.yaml")
    assert "schedule:" in compatibility_text and "cron:" in compatibility_text
    assert "ghcr.io/home-assistant/home-assistant:stable" in compatibility_text

    release_text = _text(".github/workflows/release.yaml")
    assert 'tags: ["v*"]' in release_text
    assert "toss_invest.zip" in release_text
    assert "softprops/action-gh-release@v3" in release_text
    assert "contents: write" in release_text
    assert "manifest.json" in release_text

    for name, workflow in workflows.items():
        assert workflow.get("permissions") is not None, name
        assert workflow.get("jobs"), name
    assert "actions/checkout@v6" in "".join(
        _text(f".github/workflows/{name}.yaml") for name in workflows
    )
    assert "actions/setup-python@v6" in test_text


def test_dependabot_and_bug_report_metadata_are_valid() -> None:
    dependabot = _yaml(".github/dependabot.yml")
    ecosystems = {item["package-ecosystem"] for item in dependabot["updates"]}
    assert ecosystems == {"github-actions", "pip"}

    bug = _yaml(".github/ISSUE_TEMPLATE/bug_report.yml")
    assert bug["name"] and bug["description"]
    body_ids = {item.get("id") for item in bug["body"]}
    assert {"ha_version", "integration_version", "request_id", "logs"} <= body_ids
    assert "client_secret" in _text(".github/ISSUE_TEMPLATE/bug_report.yml")


def test_mypy_uses_explicit_namespace_package_bases() -> None:
    config = tomllib.loads(_text("pyproject.toml"))
    assert config["tool"]["mypy"]["explicit_package_bases"] is True
