# Configuration

## Config Flow

Home Assistant에서 **Settings > Devices & services > Add integration > Toss Invest**를
선택합니다. 첫 단계에 Toss Open API `client_id`와 `client_secret`을 입력하면 인증을
검증하고, 다음 단계에서 조회 전용으로 사용할 `account_seq`를 선택합니다. 같은 client와
계좌 조합은 한 번만 추가할 수 있습니다. 자격 증명이 만료되거나 폐기되면 integration이
표시하는 reauthentication flow에 새 자격 증명을 입력합니다.

설정은 UI Config Flow 전용입니다. `configuration.yaml`에 OAuth 값을 넣지 마세요.

## Options

통합 카드의 **Configure**에서 변경합니다. 간격과 임계값은 UI가 아래 범위 밖의 값을
거부합니다. 시간 단위는 초, threshold 단위는 %입니다.

| Option | Default | Allowed | 설명 |
| --- | ---: | ---: | --- |
| `open_price_interval` | 30 | 10–300 | 한국/미국 장중 가격 갱신 |
| `holdings_interval` | 300 | 30–3600 | 보유 자산 갱신 |
| `closed_price_interval` | 600 | 60–3600 | 장 마감 가격 갱신 |
| `reference_interval` | 1800 | 300–21600 | 환율·시장 상태·공개 시장 데이터 갱신 |
| `candle_lookback` | 252 | 20–500 | 일봉 최대 조회 개수 |
| `max_retries` | 3 | 0–5 | 일시적 실패 재시도 횟수 |
| `request_timeout` | 10 | 5–60 | 요청 timeout |
| `enable_manual_refresh` | true | boolean | Refresh button 생성 |
| `enable_buying_power` | false | boolean | 계좌의 매수 가능 금액 조회 및 entity 생성 |
| `enable_rankings` | false | boolean | KR/US 거래대금·상승·하락 순위 조회 및 entity 생성 |
| `alert_cooldown` | 3600 | 60–86400 | 활성 경고 반복 대기시간 |
| `stock_warning_alerts_enabled` | true | boolean | Toss 종목 경고 알림 |
| `stale_data_alerts_enabled` | true | boolean | 데이터 지연 알림 |
| `api_failure_alerts_enabled` | true | boolean | 반복 API 실패 알림 |
| `daily_move_threshold` | unset | 0–100 | 종목 일일 변동 절댓값 |
| `total_return_threshold` | unset | -100–1000 | 종목 총수익률; 음수는 하락 조건 |
| `portfolio_daily_threshold` | unset | -100–100 | 포트폴리오 일일 수익률; 음수는 하락 조건 |
| `near_high_threshold` | unset | 0–100 | 기간 고가까지 남은 거리 |
| `near_low_threshold` | unset | 0–100 | 기간 저가까지 남은 거리 |
| `drawdown_threshold` | unset | 0–100 | 기간 고가 대비 낙폭 절댓값 |
| `volume_spike_threshold` | unset | 0–1000 | 평균 대비 거래량 증가율 |

`unset`인 수치 임계값은 해당 alert를 생성하지 않습니다. 옵션 저장 시 config entry가
reload되며, 선택 기능을 끄면 관련 optional entity도 제거될 수 있습니다.

**FX normalization is always enabled** for mixed-currency allocation and concentration
calculations; it is not a user option. Dashboard colors are presentation settings configured only
through the `toss-gain-color`, `toss-loss-color`, `toss-neutral-color`,
`toss-card-border-color`, and `toss-card-glow` **Lovelace theme variables** described in the
[dashboard guide](../dashboards/README.md).

## Entity defaults

기본 활성화:

- 포트폴리오 KRW/USD 매수·평가·손익, 총/일일 수익률, KR/US 및 KRW/USD 배분,
  상위 1·3종목 집중도, 환율, KR/US 시장 상태, freshness/API health
- 각 보유 종목의 현재가, 평균매수가, 수량, 매수금액, 평가액, 비용 전후 손익과 수익률,
  일일 손익·수익률 및 warning binary sensor
- KOSPI/KOSDAQ 지수와 KOSPI/KOSDAQ 개인·외국인·기관·기타법인 순매수
- Privacy mode switch, alert event; 옵션이 켜진 경우 Refresh button과 buying power sensors

Recorder 증가를 피하기 위해 기본 비활성화:

- 종목 1주/1·3·6개월/1년 수익률, 기간 고가·저가, drawdown, 변동성, 거래량 변화,
  `daily_candles`
- 한국 채권 시장 지표와 모든 rankings entities

고급 엔티티는 **Settings > Devices & services > Entities**에서 integration `Toss Invest`로
필터한 뒤 필요한 것만 활성화하세요. `daily_candles`와 rankings의 큰 attributes는
integration이 Recorder 미기록 속성으로 표시하지만, 환경에 맞는 Recorder exclusion도
적용하는 편이 안전합니다.

## Alerts

`event.toss_invest_portfolio_alert`는 `daily_move`, `total_return`, `portfolio_daily`,
`near_high`, `near_low`, `drawdown`, `volume_spike`, `stock_warning`, `stale_data`,
`api_failure` event type을 사용합니다. payload에는 `symbol`, `severity`,
`source_timestamp`가 있습니다. 금액 경고의 `observed`, `threshold`는 항상 생략됩니다.
OAuth 값과 `account_seq`도 포함하지 않습니다. 제공 blueprint 사용법은
[dashboard guide](../dashboards/README.md)를 참고하세요.
