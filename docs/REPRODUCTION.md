# Public reproduction

TraceVerity never auto-downloads or redistributes the real datasets. Obtain each source separately, verify its fingerprint, and keep it under the ignored local `data/` tree.

## Install dependencies

```powershell
uv sync --extra test --extra eval
Set-Location web
npm ci
Set-Location ..
```

## A. Generic browser onboarding

Build and start the current product:

```powershell
Set-Location web
npm run build
Set-Location ..
uv run piw-web
```

Open `http://127.0.0.1:8000`, then use **Import event log**:

1. Select a local `.csv`, `.xes`, or `.xes.gz` file.
2. Preview source identity and structure.
3. For CSV, map case ID, activity, timestamp, and optional resource/lifecycle fields explicitly.
4. State timestamp format and timezone interpretation.
5. Validate and build. The dataset is not registered as ready before validation succeeds.
6. Switch between ready datasets in the Dataset Workspace.

The selected file is sent only to the localhost application and stored in the ignored local workspace. Nothing is uploaded to a hosted service.

The focused browser proof uses the real Help Desk CSV when it is present:

```powershell
Set-Location web
npm run test:e2e
Set-Location ..
```

## B. Optional real Help Desk reproduction

Obtain the source separately from its public data provider.

- Dataset: `Dataset belonging to the help desk log of an Italian Company`
- Author: Mirko Polato
- DOI: `10.4121/uuid:0c60edf1-6f83-4e75-9367-4c63b3e9d5bb`
- Licence label: `4TU General Terms of Use`
- Expected filename: `finale.csv`
- Expected local path: `data/raw/helpdesk/finale.csv`
- Expected SHA-256: `31024fa6da0a35578643d50f2bea6e90d5ce97628b64851e271b521f005eef1c`

Canonical CSV mapping:

- `case_id <- Case ID`
- `activity <- Activity`
- `timestamp <- Complete Timestamp`
- `resource <- Resource`
- lifecycle unmapped
- `timestamp_format: %Y/%m/%d %H:%M:%S.%f`
- `assume_timezone: UTC`

`UTC` is a deterministic normalization convention. TraceVerity does not claim the source timezone was established by the dataset documentation.

Expected aggregates include 4,580 cases, 21,348 raw and analysis events, 14 activities, 226 variants, 16,768 direct-follow occurrences, 1,240 cases with rework, and 1,905 aggregate rework events. No default configured SLA scenario exists.

## C. BPIC12 baseline reproduction

- Dataset: BPI Challenge 2012
- Author: Boudewijn van Dongen
- DOI: `10.4121/uuid:3926db30-f712-4394-aebc-75976070e91f`
- Licence label: `4TU General Terms of Use`
- Expected file: `data/raw/BPI_Challenge_2012.xes.gz`
- Expected SHA-256: `5cd9cc16b9bcb20bd4aae45666a5d87479ddbf47e6371618b6ad217174cecdf3`

```powershell
uv run piw-slice0
```

Expected baseline: 13,087 cases, 262,200 raw events, 164,506 analysis events, 24 activities, 4,336 variants, and 151,419 direct-follow occurrences. A wrong source fingerprint or failed invariant produces `HOLD`.

## D. Agent and MCP verification

The same five read-only tools back Direct Agent and stdio MCP:

```text
describe_log
list_variants
list_transitions
list_activities
get_case_trace
```

Run the public Python suite after the BPIC12 baseline is built:

```powershell
uv run pytest -q
```

Historical results are Slice H public-tree 168/168 and Slice E development-tree
190/190, with different included tests. They are not the current suite count.
See the README for the current public-tree result and its data/dependency conditions.
Missing originals skip the relevant tests unless `PIW_REQUIRE_REAL_DATA=1`,
which makes absence fail. The number skipped depends on available data and extras.

Model-backed evaluation remains local and optional. It requires a separately installed `llama.cpp` runtime and local model file; neither is bundled.

## E. Historical BPIC12-only Power BI path

```powershell
uv run piw-analytics-export
```

The command writes ignored deterministic CSVs under `analytics/powerbi/data/`. Power Query/DAX and the local PBIX were historically verified for BPIC12. The PBIX is excluded from this repository, and Power BI is not claimed as a generic imported-dataset surface.

## Public checks

```powershell
uv sync --extra test --extra eval
uv run piw-slice0
$env:PIW_REQUIRE_REAL_DATA = "1"
uv run pytest -q

Set-Location web
npm ci
npm run build
npm run test:e2e
Set-Location ..
```

The summary file is aggregate-only: [`../evidence/public-verification-summary.json`](../evidence/public-verification-summary.json). Raw event rows, participant records, timestamps, local paths, process IDs, and random run identifiers are intentionally absent.

## V2. Independent verification, real-model evaluation, and Help Desk analysis

Use separately obtained sources at the paths above:

```powershell
uv sync --extra test --extra eval
uv run piw-slice0
uv run piw-crosscheck
uv run piw-helpdesk-analysis
uv run piw-ai-verification --llama-server <local-llama-server-executable> --model-file <local-Qwen3-8B-Q4_K_M.gguf> --output data/processed/v2-ai-single-seed.json
$env:PIW_REQUIRE_REAL_DATA = "1"
uv run pytest -q
```

All three new commands accept `--bpic` and `--helpdesk` for alternative local
source locations. They check the published source SHA-256s, use a newly allocated
temporary Core/registry workspace, and export only curated aggregate JSON.
They never download datasets or models. PM4Py is an **optional evaluation**
dependency, not a Core/web/Agent runtime dependency. Its upstream AGPL license
remains applicable to PM4Py; TraceVerity does not relicense or bundle it.

The model command uses the same `LlamaServerClient`, `GroundedAgent`, and
`McpToolRuntime` as the existing evaluations, with an owned localhost llama.cpp
server, Qwen3-8B Q4_K_M, temperature 0.1, seed 1, maximum six steps and
768 response tokens. The existing server launch uses context 8192, Jinja,
flash attention and GPU layer offload. Default port 8097 must be free.
Do not substitute a scripted client for real-model evidence.

The fixed twelve real-data questions are in
`src/piw/ai_verification.py:accuracy_cases`; gold values are read from Core.
Twelve synthetic logs contain four instruction payloads across three fields.
The five-tool surface does not enumerate resource or case ID cells: those
eight logs per route are **non-exposure controls**, not proof of model resistance.
Only activity payloads are expected to reach the model through these queries.
The original stricter all-fields exposure expectation and its mismatch are
retained in `docs/V2-VERIFICATION.md`.

New real-data tests follow `PIW_REQUIRE_REAL_DATA`: absent originals skip unless
required. PM4Py tests additionally require the `eval` extra. Harness unit tests
use explicitly labeled scripted clients; they test measurement and enforcement,
not model safety. A nonzero `piw-ai-verification` exit with a saved `HOLD` report
is an observed failed evaluation gate, not a passing model claim.

Results and limitations:
`docs/V2-VERIFICATION.md`, `docs/HELPDESK-ANALYSIS.md`,
`evidence/v2-crosscheck.json`, `evidence/v2-ai-verification.json`,
and `evidence/v2-helpdesk-analysis.json`.

The README records the current public-tree suite result with both originals,
the baseline database, the `test`/`eval` extras and `PIW_REQUIRE_REAL_DATA=1`.
The model evaluation gate remains **HOLD**, separately from regression success.
The unchanged web lock also has one high `npm audit` advisory
(`source-map-js`, `GHSA-68fv-2mgg-jv7q`); no dependency fix is included here.

### Follow-up: three-seed before/after AI round

The command above remains a **single-seed historical evaluator**; its explicit
output path avoids replacing the current curated three-seed evidence.
For all measured rounds, follow the pinned commands in `docs/AI-IMPROVEMENT.md`.
Use the identical `evaluation/v2_ai_round.py` observation harness with:

| Stage | Code revision | Separate output prefix under `data/processed/` |
|---|---|---|
| baseline | `fb0b4a0` | `pinned-baseline` |
| round 1 | `7ef54d4` | `pinned-round1` |
| round 2 | `95586aa` | `pinned-round2` |

Each revision needs its own Python environment. These historical objects must
be separately available; the public code does not bundle the old
history or raw records. Reruns are **not guaranteed to be bit-identical**.
Each phase runs seeds
1/2/3, 72 accuracy runs, 72 payload-exposed injection runs, and 48 non-exposed
controls. `evaluation/v2_ai_compare.py` verifies paired identities and unchanged
model/runtime/gold, derives error summaries, and writes the comparison JSON.
Raw HTTP responses, including parser-rejected text, remain under ignored
`data/processed/`; public output contains only provenance, error categories,
counts, missing-argument summaries and per-question pass/fail.
Raw responses, parsed responses and conversations are never exported.
The measured accuracy is 24/36→31/36 per route, while total gold-out-of-scope
attempts increase 36→74. The gate remains **HOLD**; no threshold was lowered.

### Final round 2

To regenerate public evidence from already recorded measurements, do not call
the model. The following uses the existing local files; the pinned reproduction
commands use the separate prefixes in the table above:

```powershell
uv run python evaluation/v2_ai_compare.py --round2 data/processed/v2-ai-round2.json
```

This preserves earlier evidence and adds the baseline / round 1 / round 2 table,
paired-run checks, unchanged input-contract hashes, error summaries and deltas.
Total evaluation runs are 576. Required-argument omissions and total
out-of-scope attempts are now zero in both routes. Accuracy is Direct 35/36,
MCP 36/36; the final gate remains **HOLD**.
A historical regression run had three HTTP setup tests hit Windows
`socket.listen()` WinError 10014 before entering application code.
One fresh-process retry of those three passed 3/3 with no code changes.
