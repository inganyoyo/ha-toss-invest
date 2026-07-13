from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any

import yaml  # type: ignore[import-untyped]
from homeassistant.components.automation.config import (
    AUTOMATION_BLUEPRINT_SCHEMA,
    async_validate_config_item,
)
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.core import State
from homeassistant.helpers.template import Template
from homeassistant.util import slugify

from homeassistant.util.yaml.loader import load_yaml


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


def _dashboard_text(name: str) -> str:
    return (DASHBOARDS / name).read_text(encoding="utf-8")


def _account_entity_id(description_key: str) -> str:
    """Derive an entity ID from the integration's device/name contract."""
    sensor_source = (ROOT / "custom_components" / "toss_invest" / "sensor.py").read_text(
        encoding="utf-8"
    )
    match = re.search(rf'_description\("{re.escape(description_key)}", "([^"]+)"', sensor_source)
    assert match is not None
    return f"sensor.{slugify('Toss Invest Portfolio')}_{slugify(match.group(1))}"


def test_all_yaml_artifacts_parse_and_each_dashboard_is_one_insertable_view() -> None:
    for name in ("toss-invest-native.yaml", "toss-invest-enhanced.yaml"):
        dashboard = _load(DASHBOARDS / name)
        assert len(dashboard["views"]) == 1
        assert dashboard["views"][0]["title"] == "Toss 주식"
        assert dashboard["views"][0]["path"] == "toss-invest"
    _load(BLUEPRINT, blueprint=True)


def test_dashboards_use_actual_portfolio_and_task_7c_market_ids() -> None:
    combined = "\n".join(
        _dashboard_text(name) for name in ("toss-invest-native.yaml", "toss-invest-enhanced.yaml")
    )
    for entity_id in (
        "sensor.toss_invest_portfolio_market_value_krw",
        "sensor.toss_invest_portfolio_daily_profit_loss_krw",
        "sensor.toss_invest_portfolio_total_return_after_cost",
        "sensor.toss_invest_portfolio_market_indicator_kospi",
        "sensor.toss_invest_portfolio_market_indicator_kosdaq",
        "sensor.toss_invest_portfolio_kospi_foreigner_net",
        "sensor.toss_invest_portfolio_kosdaq_institution_net",
        "sensor.toss_invest_portfolio_kr_market_trading_amount",
        "sensor.toss_invest_portfolio_us_top_losers",
    ):
        assert entity_id in combined
    assert "sensor.*_market_value" in combined
    assert "integration: toss_invest" in combined
    for stale_id in (
        "sensor.toss_invest_total_market_value",
        "switch.toss_invest_privacy_mode",
        "button.toss_invest_refresh",
        "event.toss_invest_alert",
    ):
        assert stale_id not in combined


def test_native_dashboard_uses_only_native_cards_and_hides_missing_optional_entities() -> None:
    text = _dashboard_text("toss-invest-native.yaml")
    dashboard = _load(DASHBOARDS / "toss-invest-native.yaml")
    assert "custom:" not in text
    assert dashboard["views"][0]["type"] == "sections"
    assert text.count("button.toss_invest_portfolio_refresh") >= 2
    assert "state_not: unavailable" in text
    assert "state_not: unknown" in text
    buying_power_ids = {
        _account_entity_id("buying_power_krw"),
        _account_entity_id("buying_power_usd"),
    }
    assert buying_power_ids == {
        "sensor.toss_invest_portfolio_krw_buying_power",
        "sensor.toss_invest_portfolio_usd_buying_power",
    }
    for buying_power in buying_power_ids:
        assert buying_power in text
    assert "sensor.toss_invest_portfolio_buying_power_" not in text
    assert "옵션 엔티티가 없으면 카드도 숨겨집니다" in text


def test_enhanced_dashboard_has_dependencies_theme_and_responsive_layout() -> None:
    text = _dashboard_text("toss-invest-enhanced.yaml")
    dashboard = _load(DASHBOARDS / "toss-invest-enhanced.yaml")
    card_types = {node.get("type") for node in _walk(dashboard) if isinstance(node, dict)}
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
    for token in (
        "toss-gain-color",
        "toss-loss-color",
        "toss-neutral-color",
        "toss-card-border-color",
        "toss-card-glow",
    ):
        assert token in text
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in text
    assert "max-width: 600px" in text
    assert "grid-template-columns: 1fr" in text
    lowered = text.lower()
    assert "#000000" not in lowered and "#ffffff" not in lowered


def test_all_dashboard_monetary_patterns_are_privacy_masked() -> None:
    enhanced = _dashboard_text("toss-invest-enhanced.yaml")
    native = _dashboard_text("toss-invest-native.yaml")
    monetary_patterns = (
        "market_value",
        "current_price",
        "buying_power",
        "daily_profit_loss",
        "profit_loss_after_cost",
        "period_high",
        "period_low",
    )
    assert (
        "const hidden = states['switch.toss_invest_portfolio_privacy_mode'].state !== 'off'"
        in enhanced
    )
    assert enhanced.count("return hidden ? '••••'") >= 3
    assert "const money = hidden ? '••••'" in enhanced
    assert "entity_id: sensor.*_current_price" not in enhanced
    for pattern in monetary_patterns:
        if pattern in native:
            assert native.find(pattern) > native.find('state: "off"')
    assert 'state: "on"' in native and "••••" in native
    assert "개발자 도구" in enhanced and "권한 경계" in enhanced


def test_selected_holding_detail_has_real_candle_chart_metrics_and_warning() -> None:
    text = _dashboard_text("toss-invest-enhanced.yaml")
    assert "entity_id: sensor.*_daily_candles" in text
    assert "entity: this.entity_id" in text
    assert "data_generator:" in text
    assert "entity.attributes.candles" in text
    assert "candle.timestamp" in text and "candle.close" in text
    assert "graph_span: 1y" in text
    for pattern in (
        "sensor.*_one_week_return",
        "sensor.*_one_month_return",
        "sensor.*_one_year_return",
        "sensor.*_period_high",
        "sensor.*_period_low",
        "sensor.*_drawdown",
        "sensor.*_historical_volatility",
        "binary_sensor.*_warning",
    ):
        assert pattern in text
    assert "하나만 활성화" in text


def test_summary_holdings_market_and_risk_sections_have_required_signals() -> None:
    text = _dashboard_text("toss-invest-enhanced.yaml")
    for signal in (
        "sensor.toss_invest_portfolio_daily_profit_loss_krw",
        "sensor.toss_invest_portfolio_total_return_after_cost",
        "sensor.toss_invest_portfolio_kr_market_status",
        "sensor.toss_invest_portfolio_us_market_status",
        "sensor.toss_invest_portfolio_data_freshness",
        "'_current_price'",
        "'_daily_return'",
        "'_total_return_after_cost'",
        "sensor.toss_invest_portfolio_market_indicator_*",
        "sensor.toss_invest_portfolio_kospi_*_net",
        "sensor.toss_invest_portfolio_kosdaq_*_net",
        "event.toss_invest_portfolio_alert",
        "binary_sensor.*_warning",
    ):
        assert signal in text
    assert "알림 임계값" in text


def test_enhanced_holding_returns_prefix_positive_values_without_changing_others() -> None:
    text = _dashboard_text("toss-invest-enhanced.yaml")
    assert "const signed = (state)" in text
    assert "value > 0 ? `+${state.state}` : state.state" in text
    assert "오늘 ${signed(daily)}% · 총 ${signed(total)}%" in text
    assert "sensor.toss_invest_portfolio_*_buying_power" in text
    assert "sensor.toss_invest_portfolio_buying_power_*" not in text


async def test_blueprint_validates_with_home_assistant_and_substitutes_required_inputs(
    hass,
) -> None:
    document = load_yaml(BLUEPRINT)
    assert isinstance(document, dict)
    blueprint = Blueprint(
        document,
        path=str(BLUEPRINT),
        expected_domain="automation",
        schema=AUTOMATION_BLUEPRINT_SCHEMA,
    )
    configured = BlueprintInputs(
        blueprint,
        {
            "use_blueprint": {
                "path": "toss_invest/toss_invest_alert.yaml",
                "input": {
                    "event_entity": "event.toss_invest_portfolio_alert",
                    "action": [{"action": "persistent_notification.create"}],
                },
            }
        },
    )
    configured.validate()
    substituted = configured.async_substitute()
    assert substituted["triggers"][0]["entity_id"] == "event.toss_invest_portfolio_alert"
    assert substituted["actions"] == [{"action": "persistent_notification.create"}]
    assert await async_validate_config_item(hass, "toss_invest_alert", substituted) is not None


def test_blueprint_forwards_only_integration_approved_payload_fields() -> None:
    blueprint = _load(BLUEPRINT, blueprint=True)
    payload = blueprint["variables"]["alert_payload"]
    assert set(payload) == {
        "event_type",
        "symbol",
        "severity",
        "source_timestamp",
        "observed",
        "threshold",
    }
    assert ".get('observed')" in payload["observed"]
    assert ".get('threshold')" in payload["threshold"]
    source = BLUEPRINT.read_text(encoding="utf-8").lower()
    for sensitive in ("client_id", "client_secret", "access_token", "account_seq"):
        assert sensitive not in source


def test_blueprint_optional_numbers_render_when_present_and_remain_none_when_omitted(
    hass,
) -> None:
    payload = _load(BLUEPRINT, blueprint=True)["variables"]["alert_payload"]
    with_numbers = SimpleNamespace(
        to_state=State(
            "event.toss_invest_portfolio_alert",
            "2026-07-13T12:00:00+09:00",
            {"observed": "4.2", "threshold": "3.0"},
        )
    )
    without_numbers = SimpleNamespace(
        to_state=State(
            "event.toss_invest_portfolio_alert",
            "2026-07-13T12:01:00+09:00",
            {},
        )
    )
    assert (
        Template(payload["observed"], hass).async_render(
            {"trigger": with_numbers}, parse_result=False
        )
        == "4.2"
    )
    assert (
        Template(payload["threshold"], hass).async_render(
            {"trigger": with_numbers}, parse_result=False
        )
        == "3.0"
    )
    assert (
        Template(payload["observed"], hass).async_render(
            {"trigger": without_numbers}, parse_result=False
        )
        == "None"
    )
    assert (
        Template(payload["threshold"], hass).async_render(
            {"trigger": without_numbers}, parse_result=False
        )
        == "None"
    )


def test_readme_documents_selection_versions_optional_absence_and_privacy_limits() -> None:
    text = (DASHBOARDS / "README.md").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "여섯 번째" in text and "views:" in text
    for dependency, version in (
        ("button-card", "7.0.1"),
        ("auto-entities", "1.16.1"),
        ("apexcharts-card", "2.2.3"),
        ("layout-card", "2.4.7"),
    ):
        assert dependency in lowered and version in text
    assert "daily_candles" in text and "하나만" in text
    assert "없으면" in text and "숨" in text
    assert "권한 경계" in text and "개발자 도구" in text and "기록" in text
    assert "toss-neutral-color" in text and "toss-card-glow" in text
