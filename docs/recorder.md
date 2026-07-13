# Recorder guidance

가격·보유 자산은 자주 갱신되므로 장기 history database가 빠르게 커질 수 있습니다.
필요한 통계만 남기고 고빈도/대형 속성 entity를 제외하세요. 아래는 시작점이며 실제 entity
ID는 **Developer Tools > States**에서 확인해야 합니다.

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.*_daily_candles
    entities:
      - event.toss_invest_portfolio_alert
      - sensor.toss_invest_portfolio_kr_market_trading_amount
      - sensor.toss_invest_portfolio_kr_top_gainers
      - sensor.toss_invest_portfolio_kr_top_losers
      - sensor.toss_invest_portfolio_us_market_trading_amount
      - sensor.toss_invest_portfolio_us_top_gainers
      - sensor.toss_invest_portfolio_us_top_losers
```

표시 이름 충돌이나 사용자 지정으로 entity ID가 달라졌다면 위 목록을 실제 ID로 바꾸세요.
`daily_candles`의 `candles`와 ranking의 `rankings`
attributes는 integration에서도 `_unrecorded_attributes`로 선언하지만, Recorder exclusion은
entity state 자체의 증가도 막습니다.

수익률 통계가 필요하면 해당 percentage sensor는 유지하고 현재가처럼 30초마다 바뀌는
monetary sensor만 선택적으로 제외할 수 있습니다. 변경 후 Home Assistant를 재시작하고
Recorder purge/repack 정책을 별도로 설정하세요. 기존 기록은 exclusion 추가만으로 즉시
삭제되지 않습니다.
