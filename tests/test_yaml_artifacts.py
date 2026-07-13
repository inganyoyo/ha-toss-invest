from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


ROOT = Path(__file__).parents[1]
DASHBOARDS = ROOT / "dashboards"
BLUEPRINT = ROOT / "blueprints" / "automation" / "toss_invest_alert.yaml"


class BlueprintLoader(yaml.SafeLoader):
    """Load Home Assistant's !input references without resolving them."""


BlueprintLoader.add_constructor(
    "!input", lambda loader, node: {"!input": loader.construct_scalar(node)}
)


def _load(path: Path, *, blueprint: bool = False) -> dict[str, Any]:
    loader = BlueprintLoader if blueprint else yaml.SafeLoader
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=loader)
    assert isinstance(document, dict)
    return document


def _walk(value: object) -> Iterator[object]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_all_yaml_artifacts_parse_and_each_dashboard_is_one_insertable_view() -> None:
    for name in ("toss-invest-native.yaml", "toss-invest-enhanced.yaml"):
        dashboard = _load(DASHBOARDS / name)
        assert len(dashboard["views"]) == 1
        assert dashboard["views"][0]["title"] == "Toss 주식"
        assert dashboard["views"][0]["path"] == "toss-invest"
    _load(BLUEPRINT, blueprint=True)


def test_dashboards_use_actual_portfolio_ids_and_dynamic_holding_patterns() -> None:
    combined = "\n".join(
        (DASHBOARDS / name).read_text(encoding="utf-8")
        for name in ("toss-invest-native.yaml", "toss-invest-enhanced.yaml")
    )
    assert "sensor.toss_invest_portfolio_market_value_krw" in combined
    assert "sensor.toss_invest_portfolio_daily_return" in combined
    assert "switch.toss_invest_portfolio_privacy_mode" in combined
    assert "button.toss_invest_portfolio_refresh" in combined
    assert "sensor.*_market_value" in combined
    assert "integration: toss_invest" in combined
    for stale_id in (
        "sensor.toss_invest_total_market_value",
        "sensor.toss_invest_daily_return",
        "sensor.toss_invest_total_return_after_cost",
        "switch.toss_invest_privacy_mode",
        "button.toss_invest_refresh",
        "event.toss_invest_alert",
    ):
        assert stale_id not in combined


def test_native_dashboard_has_no_custom_cards_and_keeps_optional_data_separate() -> None:
    path = DASHBOARDS / "toss-invest-native.yaml"
    text = path.read_text(encoding="utf-8")
    dashboard = _load(path)
    assert "custom:" not in text
    assert "옵션 데이터" in text
    assert "선택 종목 상세" in text
    assert dashboard["views"][0]["type"] == "sections"


def test_enhanced_dashboard_has_dependencies_sections_theme_and_mobile_layout() -> None:
    path = DASHBOARDS / "toss-invest-enhanced.yaml"
    text = path.read_text(encoding="utf-8")
    dashboard = _load(path)
    card_types = {
        node.get("type") for node in _walk(dashboard) if isinstance(node, dict)
    }
    assert {
        "custom:button-card",
        "custom:auto-entities",
        "custom:apexcharts-card",
        "custom:layout-card",
    } <= card_types
    for section in (
        "요약",
        "배분",
        "보유 종목",
        "선택 종목 상세",
        "시장 맥락",
        "위험 및 알림",
    ):
        assert section in text
    assert "var(--primary-text-color)" in text
    assert "var(--card-background-color)" in text
    assert "toss-gain-color" in text and "toss-loss-color" in text
    assert "#d32f2f" in text and "#1565c0" in text
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in text
    assert "max-width: 600px" in text
    assert "grid-template-columns: 1fr" in text
    lowered = text.lower()
    assert "#000000" not in lowered and "#ffffff" not in lowered


def test_enhanced_dashboard_privacy_templates_mask_money_without_claiming_security() -> None:
    text = (DASHBOARDS / "toss-invest-enhanced.yaml").read_text(encoding="utf-8")
    assert "switch.toss_invest_portfolio_privacy_mode" in text
    assert "states['switch.toss_invest_portfolio_privacy_mode'].state" in text
    assert "••••" in text
    assert "개발자 도구" in text
    assert "권한 경계" in text


def test_blueprint_requires_event_entity_and_action_and_triggers_on_state_changes() -> None:
    blueprint = _load(BLUEPRINT, blueprint=True)
    metadata = blueprint["blueprint"]
    assert metadata["domain"] == "automation"
    inputs = metadata["input"]
    event_input = inputs["event_entity"]
    assert event_input["selector"]["entity"]["filter"][0]["domain"] == "event"
    assert "default" not in event_input
    action_input = inputs["action"]
    assert "action" in action_input["selector"]
    assert "default" not in action_input

    triggers = blueprint["triggers"]
    assert triggers == [
        {
            "trigger": "state",
            "entity_id": {"!input": "event_entity"},
        }
    ]
    conditions = blueprint["conditions"]
    assert len(conditions) == 1
    assert conditions[0]["condition"] == "template"
    condition_template = conditions[0]["value_template"]
    assert "trigger.to_state is not none" in condition_template
    assert ".get('event_type') is not none" in condition_template
    assert blueprint["actions"] == {"!input": "action"}


def test_blueprint_exposes_only_non_sensitive_payload_fields_and_money_is_optional() -> None:
    blueprint = _load(BLUEPRINT, blueprint=True)
    variables = blueprint["variables"]
    assert set(variables) == {"event_type", "alert_payload"}
    payload_template = variables["alert_payload"]
    assert set(payload_template) == {"event_type", "symbol", "severity", "source_timestamp"}
    source = BLUEPRINT.read_text(encoding="utf-8").lower()
    for sensitive in (
        "client_id",
        "client_secret",
        "access_token",
        "account_seq",
        "observed",
        "threshold",
    ):
        assert sensitive not in source
    assert ".get(" in source
    assert "trigger.to_state is not none" in source


def test_readme_explains_sixth_view_insertion_dependencies_and_privacy_limits() -> None:
    text = (DASHBOARDS / "README.md").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "여섯 번째" in text and "views:" in text
    assert "단일" in text and "기존" in text
    for dependency in ("button-card", "auto-entities", "apexcharts-card", "layout-card"):
        assert dependency in lowered
    assert "native" in lowered and "custom card" in lowered
    assert "선택 사항" in text or "optional" in lowered
    assert "권한 경계" in text and "개발자 도구" in text and "기록" in text
    assert "toss-gain-color" in text and "toss-loss-color" in text
