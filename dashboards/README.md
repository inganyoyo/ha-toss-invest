# Toss Invest 대시보드

두 파일은 각각 `Toss 주식` **단일 view**만 담고 있습니다. 완성된 새 대시보드가
아니므로 파일 전체를 기존 Raw configuration에 덮어쓰지 마세요. 기존 대시보드의
`views:` 목록 아래에 선택한 파일의 첫 번째 `- title: Toss 주식` 블록 하나를 붙여
`홈`, `전기`, `시스템`, `차량`, `로또` 다음 **여섯 번째 view**로 삽입합니다. 기존
설정에도 `path: toss-invest`가 있다면 경로를 바꾸세요.

## 두 버전

- `toss-invest-native.yaml`: Home Assistant 기본 카드만 사용합니다. custom card가 없어도
  동작하며, 선택 사항인 주문 가능 금액이나 수동 새로고침 엔티티가 없더라도 나머지
  섹션은 계속 유용합니다. 보유 종목 ID는 이름 기반으로 동적 생성되므로 필요한 종목은
  UI 편집기에서 추가합니다.
- `toss-invest-enhanced.yaml`: 보유 종목과 경고를 자동 탐색하고, 2열 데스크톱/1열 모바일
  레이아웃과 차트를 제공합니다.

Enhanced 버전은 HACS Frontend에서 다음 리소스를 설치하고 Lovelace Resources에
등록해야 합니다. 링크에서 배포되는 현재 안정 버전을 함께 업데이트하는 방식을
권장합니다.

- [button-card](https://github.com/custom-cards/button-card)
- [auto-entities](https://github.com/thomasloven/lovelace-auto-entities)
- [apexcharts-card](https://github.com/RomRider/apexcharts-card)
- [layout-card](https://github.com/thomasloven/lovelace-layout-card)

리소스가 없으면 enhanced view만 렌더링되지 않습니다. Toss Invest 통합, native 엔티티,
native 대시보드는 custom card와 독립적입니다.

## 엔티티와 선택 사항

포트폴리오 엔티티는 `sensor.toss_invest_portfolio_*`이고, 컨트롤/이벤트도
`switch.toss_invest_portfolio_*`, `button.toss_invest_portfolio_*`,
`event.toss_invest_portfolio_*`입니다. 보유 종목은 심볼이 아니라 표시 이름에서 ID가
만들어집니다. 그래서 enhanced 파일은 특정 종목 ID를 고정하지 않고 `integration:
toss_invest`와 `sensor.*_market_value`, `sensor.*_current_price`,
`binary_sensor.*_warning` 패턴으로 찾습니다.

주문 가능 금액은 해당 옵션을 켰을 때만 생성됩니다. 기간 수익률, 기간 고가/저가,
낙폭, 변동성 같은 고급 엔티티는 기본 비활성화입니다. 새로고침 버튼도 옵션에서 끌 수
있습니다. 없는 선택 엔티티는 enhanced의 자동 목록에서 숨겨지고, native에서는
`옵션 데이터` 카드에만 분리되어 있습니다.

## 테마와 한국식 등락 색상

표면과 텍스트는 `--card-background-color`, `--primary-text-color`,
`--divider-color` 같은 Home Assistant 테마 변수를 사용합니다. 기본 등락 색상은 한국식
표현에 맞춰 상승 빨강, 하락 파랑입니다. 테마 YAML에서 다음 변수를 정의해 바꿀 수
있습니다.

```yaml
toss-gain-color: "#d32f2f"
toss-loss-color: "#1565c0"
```

## 개인정보 보호 제한

`switch.toss_invest_portfolio_privacy_mode`가 켜지면 대시보드의 금액 표시 템플릿은
`••••`로 바뀝니다. native 버전도 조건부 카드로 금액 카드를 숨깁니다. 하지만 이것은
화면 가림 기능일 뿐 **권한 경계**가 아닙니다. 원본 센서 상태는 바뀌지 않으므로 해당
Home Assistant 사용자는 개발자 도구, 엔티티 상세, 자동화, API 또는 Recorder **기록**에서
금액을 볼 수 있습니다. 실제 접근 제어가 필요하면 Home Assistant 사용자와 대시보드
권한을 별도로 구성하세요.

## 알림 블루프린트

`blueprints/automation/toss_invest_alert.yaml`을 Home Assistant의 automation blueprint
경로에 복사한 뒤 `event.toss_invest_portfolio_alert`와 실행할 action을 선택합니다.
action 템플릿에서는 `event_type`과 `alert_payload.event_type`, `.symbol`, `.severity`,
`.source_timestamp`를 사용할 수 있습니다. EventEntity의 상태가 바뀔 때마다 실행되며,
금액 필드의 존재를 전제로 하지 않습니다.
