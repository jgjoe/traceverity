# TraceVerity — 업무 프로세스 분석 워크벤치

**업무 이벤트 로그에서 처리 시간과 재작업을 조회하는 분석 도구**

로컬 CSV, XES, XES.GZ 로그를 브라우저에서 가져와 필드를 매핑하고 검증한 뒤 처리 순서, 소요 시간과 재작업을 분석합니다. 핵심 지표는 Python/DuckDB 한 곳에서 계산하고 웹, AI 에이전트와 MCP가 같은 결과를 읽습니다.
서로 다른 실제 업무 로그 2종(BPI Challenge 2012, Italian Help Desk)에서 같은 엔진으로 검증했습니다.

![가져오기 패널 아래에서 Italian Help Desk 데이터를 분석 중인 워크벤치](docs/images/traceverity-overview.png)

---

## 주요 기능

- **로그 가져오기** — 로컬 `.csv`·`.xes`·`.xes.gz` 파일을 미리보기 → 필드 매핑 → 시간 형식·시간대 지정 → 검증 → 등록 순서로 가져옵니다
- **처리 흐름 분석** — 처리 경로(variant), 직접 이어지는 단계, 소요시간, 재작업, 단계 사이 간격을 계산합니다
- **AI 질의** — 읽기 전용 도구 5개(`describe_log`, `list_variants`, `list_transitions`, `list_activities`, `get_case_trace`)를 Direct Agent와 stdio MCP로 제공합니다
- **BI 연동** — BPI Challenge 2012 분석 결과를 Power BI 보고서용 데이터로 내보냅니다

![실제 Help Desk CSV를 가져와 4,580건 케이스를 선택한 화면](docs/images/traceverity-helpdesk-onboarding.png)

## 설계 판단

### 지표 계산은 한 곳에서만

웹 화면·BI·AI가 지표를 각자 계산하면 같은 로그에서 다른 숫자가 나옵니다.
처리 순서, lifecycle 관점, 처리 경로, 직접 전이, 소요시간, 재작업, 임계값 시나리오를 버전이 붙은 Core 한 곳에 두고,
잘못된 원천 데이터나 어긋난 로컬 상태는 결과를 내지 않고 멈추게(fail closed) 했습니다.

```text
로컬 CSV / XES / XES.GZ
        |
        v
미리보기, 필드 매핑, 시간 해석, 검증
        |
        v
DatasetRegistry -> DatasetResolver -> CoreReadSurface
        |                         Python / DuckDB Core
        +--> localhost FastAPI --> React 워크벤치
        +--> 읽기 전용 도구 5개 --> Direct Agent
        |                       \-> stdio MCP
        \--> BPIC12 내보내기 ----> Power BI 보고서
```

### AI는 계산하지 않고 읽기만 한다

Direct Agent와 stdio MCP는 같은 스키마, `DatasetResolver`, `CoreReadSurface`를 씁니다. MCP는 전송 방식일 뿐 두 번째 계산 엔진이 아닙니다.
검사 대상은 **구조화된 fact 값과 그 근거 ID가 도구 반환값에 대응하는지**입니다.
자유 서술 explanation의 의미적 진실성은 검사 범위 밖이며, `UNAVAILABLE` 응답의 설명도 자동으로 사실 검증되는 것은 아닙니다.

### 가져오기는 명시적으로

CSV는 케이스 ID·활동·시각 매핑을 필수로, 자원·lifecycle은 선택 매핑으로 받습니다.
시간대는 사용자가 지정한 규칙으로 일관되게 정규화하고, 검증을 통과한 데이터만 분석 대상으로 등록합니다.

## 검증 결과

같은 계산 엔진을 바꾸지 않고 두 실제 이벤트 로그에서 집계 값을 독립적으로 다시 계산해 확인했습니다.

| 검증 집계 | BPI Challenge 2012 | Italian Help Desk |
|---|---:|---:|
| 케이스 | 13,087 | 4,580 |
| 원본 이벤트 | 262,200 | 21,348 |
| 분석 이벤트 | 164,506 | 21,348 |
| 활동 | 24 | 14 |
| 처리 경로(variant) | 4,336 | 226 |
| 직접 전이 | 151,419 | 16,768 |
| 재작업 케이스 | 7,019 | 1,240 |
| 재작업 이벤트 합계 | 57,556 | 1,905 |

| 검증 단계 | 결과 |
|---|---:|
| 현재 공개 트리 Python 회귀 테스트 | 226 / 226 검증 통과 |
| 웹 프로덕션 빌드 | 통과 |
| Playwright 전체 흐름 | 2 / 2 통과 |

현재 결과의 조건은 BPIC12·Help Desk 원본과 BPIC12 기준 DB를 준비하고,
`uv sync --extra test --extra eval`로 PM4Py를 포함한 평가 의존성을 설치한 뒤
`PIW_REQUIRE_REAL_DATA=1`로 실행하는 것입니다.
전체 실행의 223건은 바로 통과했고, Windows socket의 WinError 10014로 실패한
3건은 코드 변경 없이 한 번 재시도해 모두 통과했습니다.
원본이 없으면 관련 테스트는 사유와 함께 skip하며, 위 환경 변수 설정 시에는 실패합니다.
원본 데이터셋은 저장소에 포함하지 않습니다. 재현 절차는 [`docs/REPRODUCTION.md`](docs/REPRODUCTION.md)에 있습니다.

용어: **gold**는 Core가 제공한 기준 정답, **grounding**은 구조화된 답과 도구 근거의 대조,
**seed**는 난수 초기화 값, **HOLD**는 사전 평가 기준 미달 상태입니다.
테스트 통과와 모델 평가 HOLD는 다른 결과이며, 같은 seed도 생성 결과의 완전한 재현을 보장하지 않습니다.

### 과거 검증 기록 — 현재 테스트 수와 별개

개발본 Slice E는 Python **190/190**, 초기 공개 트리 Slice H는 **168/168**을 기록했습니다.
당시 Agent/MCP 각 12/12와 경로 간 22/22 일치, 경로 검사 179건 중 0건도 별도 시나리오의 역사적 집계입니다.
이 기록은 [`evidence/public-verification-summary.json`](evidence/public-verification-summary.json)에 보존하며,
현재 공개 트리 결과나 아래 3-seed AI 평가의 분모와 합산하지 않습니다.

![처리 경로·전이·활동 집계 화면](docs/images/traceverity-process-patterns.png)

## 기술 스택

| 영역 | 기술 |
|---|---|
| 계산 엔진 | Python, DuckDB |
| API·웹 | FastAPI, React, TypeScript, Vite |
| AI 연동 | 읽기 전용 도구 5개, Direct Agent, stdio MCP |
| BI | Power BI (Power Query, DAX) |
| 테스트 | pytest, Playwright |

## 실행

BPI Challenge 2012 또는 Italian Help Desk 로그를 원래 제공처에서 받아 `data/` 아래에 둡니다. 파일 이름·지문·CSV 매핑은 [`docs/REPRODUCTION.md`](docs/REPRODUCTION.md)에 있습니다.

```powershell
uv sync --extra test --extra eval
uv run piw-slice0
$env:PIW_REQUIRE_REAL_DATA = "1"
uv run pytest -q

Set-Location web
npm ci
npm run build
npm run test:e2e
```

모델을 쓰는 Agent/MCP 평가는 로컬 `llama.cpp` 런타임과 모델 파일이 있으면 추가로 실행할 수 있습니다.

## 추가 검증: 독립 계산·실제 AI 평가·분석 사례

- **PM4Py 교차검증** — 두 실제 로그의 전체 variant 빈도·DFG·17,667개 개별 케이스
  duration·재작업을 독립 계산해 정의 정렬 후 불일치 0을 확인했습니다.
- **실제 로컬 모델 평가** — Qwen3-8B Q4_K_M의 원문을 진단하고 baseline·round 1·
  최종 round 2를 3 seed·Direct/MCP로 총 576회 비교했습니다.
  정확도는 Direct 24→31→35/36, MCP 24→31→36/36입니다. 필수 인자 누락 요청과
  전체 gold 범위 밖 요청은 round 1의 경로별 74회에서 최종 0회로 줄었습니다.
  Direct의 초과 fact 1건 때문에 전체 gate는 **HOLD**로 유지합니다.
  12종 activity 공격은 경로별·단계별 36회 실제 노출했고 비노출 대조군을 분리했습니다.
  금지 권한 동작·허용 grounding 이탈·경로 패턴 검출은 0이지만 보편적 안전 보장은 아닙니다.
- **Help Desk 사례 분석** — 재작업 상위 세 활동의 비중 85.67%와 긴 duration 관련
  경로를 집계하고, 실제 대기시간·업무 SLA·인과 효과와 구분한 개선 가설을 제시했습니다.

실행 명령·실측·한계는 [`docs/V2-VERIFICATION.md`](docs/V2-VERIFICATION.md),
분석 전문은 [`docs/HELPDESK-ANALYSIS.md`](docs/HELPDESK-ANALYSIS.md)에 있습니다.
PM4Py는 선택적 평가 의존성이며 Core나 Agent의 계산 경로를 대체하지 않습니다.
AI 평가의 오류 유형·실패·전후 수치와 revision별 재현 방법은
[`docs/AI-IMPROVEMENT.md`](docs/AI-IMPROVEMENT.md)에 있습니다.
공개 AI 증거는 집계와 문항별 통과 여부만 담고, 모델 원문·파싱 결과·대화는 포함하지 않습니다.

## 라이선스

이 저장소의 소스와 문서는 [MIT License](LICENSE)를 따릅니다.
BPI Challenge 2012와 Italian Help Desk 데이터셋은 각 제공처의 조건을 따르며 이 저장소에 포함하거나 재라이선스하지 않습니다([NOTICE](NOTICE)).

## 만든 사람

**Jigwan Joe** — Backend · Data

- GitHub: [@jgjoe](https://github.com/jgjoe)
- Email: jigwan.joe@gmail.com
