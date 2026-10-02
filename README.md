# TraceVerity — 업무 프로세스 분석 워크벤치

**업무 이벤트 로그를 브라우저에서 불러와 실제 처리 흐름을 분석하고, 웹·AI·MCP가 같은 계산 결과를 쓰도록 만든 분석 도구**

로컬 CSV·XES·XES.GZ 이벤트 로그를 브라우저에서 가져와 필드를 매핑·검증한 뒤 바로 분석합니다.
핵심 지표는 Python/DuckDB 계산 엔진 한 곳에서만 만들고, 웹 화면·AI 에이전트·MCP는 그 결과를 읽기만 합니다.
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
답에 들어간 값이 도구가 돌려준 사실로 추적될 때만 답을 받아들이고, 도구로 확인할 수 없는 질문은 추정해 답하지 않습니다.

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
| Python 회귀 테스트 | 190 / 190 통과 |
| 웹 프로덕션 빌드 | 통과 |
| Playwright 전체 흐름 | 2 / 2 통과 |
| Direct Agent | 12 / 12 통과 |
| MCP Agent | 12 / 12 통과 |
| Direct·MCP 결과 비교 | 22 / 22 일치 |
| 근거 이탈·금지 동작 | 0 / 0 |
| 로컬 경로 노출 검사 | 응답 179건 중 0건 |

공개 저장소의 Python 테스트는 168 / 168 통과합니다. 원본 데이터셋은 저장소에 넣지 않았고, 출처·지문·집계 값은 [`evidence/public-verification-summary.json`](evidence/public-verification-summary.json)에, 재현 절차는 [`docs/REPRODUCTION.md`](docs/REPRODUCTION.md)에 있습니다.

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
uv sync --extra test
uv run piw-slice0
uv run pytest -q

Set-Location web
npm ci
npm run build
npm run test:e2e
```

모델을 쓰는 Agent/MCP 평가는 로컬 `llama.cpp` 런타임과 모델 파일이 있으면 추가로 실행할 수 있습니다.

## 라이선스

이 저장소의 소스와 문서는 [MIT License](LICENSE)를 따릅니다.
BPI Challenge 2012와 Italian Help Desk 데이터셋은 각 제공처의 조건을 따르며 이 저장소에 포함하거나 재라이선스하지 않습니다([NOTICE](NOTICE)).

## 만든 사람

**Jigwan Joe** — Backend · Data

- GitHub: [@jgjoe](https://github.com/jgjoe)
- Email: jigwan.joe@gmail.com
