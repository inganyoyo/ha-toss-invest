# Troubleshooting

## Integration cannot be added or asks for reauthentication

- 시스템 시간이 정확한지 확인합니다.
- Toss에서 `client_id`/`client_secret`이 활성 상태인지 확인하고 복사할 때 공백을 제거합니다.
- 기존 계좌를 중복 추가하지 않았는지 확인합니다.
- credential을 재발급했다면 integration의 reauthentication flow에 새 값을 입력합니다.

로그를 공유할 때 credential, bearer token, `account_seq`와 금액을 삭제하세요.

## Rate limited, stale, or unavailable entities

Toss가 429와 `Retry-After`를 반환하면 integration은 endpoint group별 제한을 적용합니다.
장중 주기를 지나치게 낮추지 말고 기본 30초부터 시작하세요. 일시적 network/5xx는 bounded
retry를 사용합니다. 일부 API group만 실패하면 그 group에 의존하는 entity만 unavailable이
될 수 있습니다. `data_freshness`, `api_health`, market status를 함께 확인하세요.

## Toss request ID로 진단

API 오류에는 응답 body 또는 `X-Request-Id`의 Toss **request ID**가 포함될 수 있습니다.
Home Assistant 로그에서 오류 code와 request ID, 발생 시각, endpoint 종류, Home Assistant와
integration 버전을 기록하세요. credential이나 응답 payload 전체는 공유하지 마세요. Toss
지원에 문의할 때 이 request ID를 사용하면 서버 측 요청 추적에 도움이 됩니다.

debug log를 짧게 켜려면 다음을 사용하고 재현 후 즉시 원래 level로 되돌립니다.

```yaml
logger:
  logs:
    custom_components.toss_invest: debug
```

## Dashboard cards are missing

옵션을 끄거나 entity registry에서 비활성화한 optional entity는 정상적으로 숨겨집니다.
Enhanced dashboard에는 layout-card 2.4.7, button-card 7.0.1, auto-entities 1.16.1,
apexcharts-card 2.2.3가 필요합니다. 브라우저 cache를 비우고 Lovelace Resources 등록을
확인하세요. 자세한 절차는 [dashboard guide](../dashboards/README.md)에 있습니다.

## Safe real-API testing

실제 **real API** 검증은 `dev/compose.yaml` 개발 인스턴스와 조회 전용 credential에서만
수행하세요. 운영 Home Assistant에 개발 component를 bind mount하지 말고 fixture나 issue에
실제 응답을 저장하지 마세요. 이 integration에는 order endpoint가 없지만 외부 script나
다른 client와 같은 credential을 공유하지 않는 것이 안전합니다.

버그 보고에는 Home Assistant 버전, integration 버전, 재현 단계, sanitized log와 request ID만
포함하세요.
