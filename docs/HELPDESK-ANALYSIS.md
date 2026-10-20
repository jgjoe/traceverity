# Italian Help Desk: 재작업과 긴 기록상 소요시간

## 자료·방법

별도 취득한 `finale.csv`의 SHA-256을 공개 지문과 대조한 뒤 TraceVerity Core로
4,580개 케이스, 21,348개 이벤트, 14개 활동, 226개 variant, 16,768개 직접 전이를
집계했다. lifecycle 매핑이 없어 raw와 COMPLETE 분석 이벤트 수는 같다.
계산 계약은 `slice0-metrics-v1`, 타임스탬프 해석은 UTC 정규화 규약이다.
UTC가 원본의 실제 업무 시간대라는 뜻은 아니다.

수치의 정본은 `evidence/v2-helpdesk-analysis.json`이다. 원천 행, 케이스 ID,
자원 식별자, 원천 timestamp는 이 보고서에 공개하지 않는다.
PM4Py 교차검증은 `evidence/v2-crosscheck.json`에 있으며 분석 수치는 Core에서 가져온다.
기간의 일(day) 표시는 `ms / 86,400,000`을 소수 둘째 자리로 반올림한 것이다.
median/p90은 보간하지 않는 discrete percentile이다.

## 1. 재작업은 어디에 집중되는가

재작업은 동일 케이스 내 동일 활동의 첫 발생 이후 이벤트다. 오류 또는 불필요한
업무라고 단정하지 않는다. 재작업 케이스는 1,240개(27.07%), 초과 이벤트 합계는 1,905개다.

| 활동 | 재작업 이벤트 | 전체 재작업 이벤트 중 비중 |
|---|---:|---:|
| Take in charge ticket | 775 | 40.68% |
| Assign seriousness | 443 | 23.25% |
| Resolve ticket | 414 | 21.73% |
| Wait | 227 | 11.92% |

상위 세 활동이 1,632개, 즉 85.67%를 차지한다.
활동별 재작업 케이스 수는 서로 겹칠 수 있으므로 합산해 전체 1,240개와 비교하면 안 된다.

재작업이 있는 1,240개 케이스의 기록상 duration 중앙값은 3,530,731,000ms
(40.86일), 없는 3,340개는 3,349,305,000ms(38.77일)다.
두 집단은 난이도·업무 유형을 통제하지 않았으므로 차이를 재작업의 인과 효과로 해석할 수 없다.

## 2. 어떤 경로가 긴 duration과 연관되는가

전체 raw first-to-last duration 중앙값은 3,442,862,000ms(39.85일),
p90은 4,579,685,000ms(53.01일)이다.
비교 가능한 크기를 확보하기 위해 30개 케이스 이상인 variant만 duration 순위에 포함했다.

| 활동 경로 | 케이스 | duration 중앙값(ms) |
|---|---:|---:|
| Assign seriousness → Take in charge ticket → Resolve ticket → Closed | 2,366 | 3,303,508,000 |
| Assign seriousness → Take in charge ticket → Wait → Resolve ticket → Closed | 552 | 3,346,432,000 |
| Assign seriousness → Take in charge ticket → Wait → Take in charge ticket → Resolve ticket → Closed | 228 | 3,631,651,000 |
| Insert ticket → Assign seriousness → Take in charge ticket → Resolve ticket → Closed | 69 | 3,798,463,000 |

가장 흔한 variant는 전체의 51.66%다. `Wait` 뒤에 다시 담당 처리가 나타나는
228개 경로는 가장 흔한 경로보다 중앙값이 크다. 반면 빈도 30개 이상 경로 중
중앙값이 가장 큰 것은 69개 `Insert ticket` 시작 경로다.
즉, 모든 긴 경로를 재작업 하나로 설명할 수 없다.
전체 상위 목록과 variant ID는 JSON의 `long_duration_variants`에 있다.

## 3. 전이의 간격과 전이 보유 케이스의 duration은 다르다

| 직접 전이 | 발생 수 / 보유 케이스 | 보유 케이스 duration 중앙값(ms) | observed gap 중앙값(ms) |
|---|---:|---:|---:|
| Take in charge ticket → Create SW anomaly | 53 / 50 | 4,317,242,000 | 222,000 |
| Create SW anomaly → Resolve ticket | 42 / 39 | 4,061,412,000 | 1,300,304,000 |
| Take in charge ticket → Take in charge ticket | 99 / 84 | 3,628,694,000 | 63,466,000 |
| Resolve ticket → Closed | 4,558 / 4,557 | 3,441,029,000 | 2,592,070,000 |

전이가 반복될 수 있으므로 발생 수와 고유 케이스 수를 구분했다.
한 케이스는 여러 전이 집단에 속할 수 있다. 전이 집단은 서로 배타적이지 않다.
특히 첫 행은 보유 케이스 duration은 길지만 그 전이 자체의 중앙 간격은 222초다.
해당 단계가 긴 duration의 원인이라는 해석은 성립하지 않는다.

`Resolve ticket → Closed`의 observed gap 중앙값은 약 30.00일로, 전체 duration
해석에서 종결 기록 규칙 확인이 중요함을 보여준다. 실제 작업, queue, 고객 응답,
유휴, 일괄 종결 중 무엇인지는 로그만으로 식별할 수 없다.
활동 이름 `Wait`도 실제 waiting time의 계측 근거가 아니다.

## 4. 개선 가설 — 효과가 입증된 권고가 아님

1. **담당 재지정·재분류 기준 점검 가설.** 담당 처리와 심각도 지정이 재작업의
   1,218개를 차지한다. 이 두 활동이 반복되는 표본의 업무 사유를 현업과 확인하고,
   누락 정보·분류 기준·정당한 이관을 구분한 뒤 입력 체크리스트를 소규모 시험할 수 있다.
   반복이 오류라는 증거도, 체크리스트가 duration을 줄인다는 증거도 아직 없다.
2. **SW anomaly 경로의 조기 분류 가설.** 관련 전이 보유 케이스는 39~50개이며
   duration 중앙값이 전체보다 크다. 난이도와 분류 사유를 추가로 확보해
   조기 전문 처리의 시험 대상을 정할 수 있다. 작은 집단의 상관관계를 전체 업무로
   일반화하거나 원인으로 확정하지 않는다.
3. **종결 기록 정의 확인 가설.** Resolve→Closed 간격이 duration을 크게 구성할
   수 있다. 자동/수동 종결 및 배치 규칙을 먼저 확인하고, 업무상 해결 시점과
   기록상 종료 시점을 구분하는 것이 분석 해석을 개선할 수 있다.
   현재 데이터만으로 해결시간 지표나 business SLA를 새로 만들지 않는다.

## 재현과 한계

```powershell
uv sync --extra test --extra eval
uv run piw-helpdesk-analysis
uv run piw-crosscheck
```

기본 입력은 `docs/REPRODUCTION.md`의 두 원본 경로다. 다른 로컬 경로라면 두 명령의
`--bpic`과 `--helpdesk`를 지정한다. 자동 다운로드는 없다.
분석 스크립트는 임시 workspace에서 Core를 빌드하고 공개 가능한 집계 JSON만 저장한다.
도표는 추가하지 않았다. JSON의 활동/variant/전이 집계로 표를 재현할 수 있다.

세 보존 검사(variant 케이스 합, 직접 전이 발생 합, 재작업 이벤트 합)는 모두 통과했다.
PM4Py와 정의 정렬 후 전체 variant 빈도·DFG·케이스별 duration·재작업 불일치는 0이다.
관찰 연구, 단일 조직, 미확인 업무 시간대·종결 규칙, 교란 변수 미통제가 남는다.
고객 응답 속도, 실제 대기시간, 업무 SLA 위반, 개선 효과는 검증하지 않았다.
