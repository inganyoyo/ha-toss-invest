# Toss Invest for Home Assistant

Toss Securities Open API의 보유 자산, 시세, 시장 지표와 경고를 Home Assistant 엔티티로
제공하는 비공식 커스텀 통합입니다. Home Assistant 2026.7.2 이상과 Python 3.14를
지원합니다.

> [!IMPORTANT]
> 버전 1은 **read-only**입니다. OAuth 토큰 발급을 제외한 데이터 요청은 GET뿐이며,
> **No order** endpoint, 주문 조회, 주문 생성, 정정, 취소 또는 자동매매 기능을 구현하지
> 않습니다. Toss 또는 Home Assistant 공식 통합이 아닙니다.

## 설치

### HACS (권장)

1. HACS에서 우측 상단 메뉴의 **Custom repositories**를 엽니다.
2. `https://github.com/inganyoyo/ha-toss-invest`를 추가하고 category를 `Integration`으로
   선택합니다.
3. `Toss Invest`를 설치한 뒤 Home Assistant를 재시작합니다.

수동 설치는 이 저장소의 `custom_components/toss_invest` 디렉터리를 Home Assistant의
`<config>/custom_components/toss_invest`로 복사한 뒤 재시작합니다.

## 빠른 설정

1. Toss Invest Open API에서 OAuth `client_id`와 `client_secret`을 발급받습니다.
2. Home Assistant에서 **Settings > Devices & services > Add integration > Toss Invest**를
   선택합니다.
3. 두 값을 입력하고 조회할 계좌를 선택합니다. `client_secret`은 비밀번호 필드로
   입력되지만 Home Assistant config entry 저장소에는 복호화 가능한 형태로 보관됩니다.
4. 생성된 엔티티를 확인하고 필요하면 통합의 Configure 메뉴에서 주기, 선택 데이터,
   개인정보 표시와 알림을 조정합니다.

모든 옵션, 범위와 엔티티 기본값은 [configuration.md](docs/configuration.md)를 참고하세요.

## 대시보드와 알림

- `dashboards/toss-invest-native.yaml`: Home Assistant 기본 카드만 사용합니다.
- `dashboards/toss-invest-enhanced.yaml`: layout-card 2.4.7, button-card 7.0.1,
  auto-entities 1.16.1, apexcharts-card 2.2.3가 필요합니다.
- `blueprints/automation/toss_invest_alert.yaml`: EventEntity 경고를 자동화 action으로
  전달합니다.

설치 및 선택 엔티티 사용법은 [dashboard guide](dashboards/README.md)에 있습니다.

## 보안과 개인정보

`client_secret`을 YAML, 대시보드, issue 또는 로그에 붙여 넣지 마세요. 저장소를 정기적으로
백업하고 Home Assistant 사용자·네트워크 접근 권한을 제한하세요.

**Privacy mode is not an authorization boundary**입니다. Privacy mode는 제공 대시보드의
금액을 가리는 표시 기능이며 원본 entity state, Developer Tools, API, 자동화 또는 Recorder
기록을 숨기거나 암호화하지 않습니다. 자세한 위협 모델과 알림 payload 정책은
[privacy.md](docs/privacy.md), 장기 기록 제한은 [recorder.md](docs/recorder.md)를
확인하세요.

## 문제 해결과 개발

재인증, rate limit, 데이터 지연 및 Toss request ID를 이용한 진단은
[troubleshooting.md](docs/troubleshooting.md)를 따르세요. 실제 API 자격 증명을 사용하는
테스트는 개발 Home Assistant에서만 수행하고 운영 계정이나 운영 인스턴스에 적용하지
마세요.

로컬 검증:

```bash
python -m pip install -e '.[dev]' 'homeassistant==2026.7.2'
pytest -q
ruff check .
ruff format --check .
mypy custom_components/toss_invest
```

## 문서

- [Configuration and entity defaults](docs/configuration.md)
- [Privacy and credential handling](docs/privacy.md)
- [Recorder exclusions](docs/recorder.md)
- [Troubleshooting](docs/troubleshooting.md)

## License

[MIT](LICENSE)
