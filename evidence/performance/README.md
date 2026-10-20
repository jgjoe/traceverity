# V2 performance: measure first, fix the import bottleneck

## Verdict and ownership

**The pre-registered improvement criterion is met at 2M synthetic events: import
median fell 38.68%, from 24.095 s to 14.775 s.** It is not met by elapsed time on
either real dataset (BPIC12: 26.39%; Help Desk: 4.51%). This is not a claim that
every dataset or query improved by 30%. Peak memory is essentially unchanged.

The user's prediction was **aggregation / overall summary**; the measurement
contradicted it: **import was slowest on every dataset**. The scale limit was
unknown beforehand; this experiment establishes completion through 2M events,
not a maximum supported scale or a production concurrency limit.

The user set requirements, the hypothesis and acceptance criteria. AI coding
tools investigated, implemented, measured and tested. User review/acceptance of
this result remains pending; do not describe this as user hand-written code.

## Revisions and method

- Fetched `origin/main`: `ecb7504` (PR #3 merged). Branch:
  `v2/performance-current`, based on that revision.
- Original interrupted work is preserved on `v2/performance` at `8f1fcad`;
  its current-main cherry-pick is `60c42c6`. Both retain the original four files.
- `baseline.json` is the untouched historical 2026-10-19 run on `2124fa7`,
  **not** the comparator for this change.
- `before-current-main.json`: unoptimized current-main implementation with
  benchmark harness `60c42c6`; results committed as `8516117`.
- `after-single-pass.json`: implementation `95164c9`. Measurement/profile
  execution window: 2026-10-20 16:12–16:33 UTC. No benchmark runs overlapped
  tests, builds or the other benchmark block.
- Fresh process **per phase per run**; one discarded warmup then five runs,
  median/min/max. Sources and query databases prepared before measurement.
  Elapsed time excludes process startup, descriptor construction and case-ID
  selection; import includes ingestion, report metrics and validation.
  Peak RSS is the entire child process's peak working set, including setup,
  not incremental memory or the sum of phase peaks. Warmed filesystem cache,
  not a cold-disk benchmark. Profiles are separate, instrumented runs.
- Real data and seeded synthetic data are separate below. The seed is
  `20261019`; dataset fingerprints, counts and sizes match before/after.
  Source files and raw response snapshots remain in ignored `data/`.
- Same environment before/after: Windows 11 `10.0.26300`, AMD64 Family 25
  Model 33 Stepping 2, 16 logical CPUs, 34,270,076,928 bytes RAM;
  Python 3.13.15, DuckDB 1.5.5. Installed with
  `uv sync --extra test --extra eval` (including PM4Py 2.7.23.11,
  pandas 3.0.6). Historical results are not substituted for this fresh baseline.

## Import elapsed time

Seconds: **median [min, max]** across five measured fresh processes.
Reduction = `100 * (before_median - after_median) / before_median`.

| Dataset | Cases / events | Before | After | Reduction |
|---|---:|---:|---:|---:|
| Real: BPIC12 | 13,087 / 262,200 | 7.683 [7.590, 7.730] | 5.656 [5.461, 5.775] | 26.39% |
| Real: Help Desk | 4,580 / 21,348 | 1.188 [1.169, 1.202] | 1.135 [1.103, 1.165] | 4.51% |
| Synthetic: 100k | 5,056 / 100,000 | 1.767 [1.736, 1.771] | 1.574 [1.567, 1.613] | 10.91% |
| Synthetic: 500k | 25,011 / 500,000 | 5.444 [5.409, 5.583] | 4.416 [4.358, 4.619] | 18.89% |
| Synthetic: 1M | 49,941 / 1,000,000 | 11.076 [10.325, 14.716] | 7.937 [7.740, 8.192] | 28.34% |
| Synthetic: 2M | 99,864 / 2,000,000 | 24.095 [18.546, 26.218] | 14.775 [14.465, 15.125] | **38.68%** |

## Total phase budget

**Total means the sum of the six independently measured phase medians**:
import plus `describe_log`, `list_variants`, `list_transitions`,
`list_activities`, `get_case_trace`. It is not a measured end-to-end latency,
nor a median of per-run totals. Component min/max and peak RSS for all 36
dataset/phase combinations are in the before/after JSON files; adding component
extrema would not give observed total extrema.

| Dataset | Before total (s) | After total (s) | Reduction |
|---|---:|---:|---:|
| Real: BPIC12 | 8.868 | 6.865 | 22.59% |
| Real: Help Desk | 1.986 | 1.920 | 3.34% |
| Synthetic: 100k | 2.732 | 2.591 | 5.14% |
| Synthetic: 500k | 7.279 | 6.120 | 15.91% |
| Synthetic: 1M | 13.993 | 10.925 | 21.93% |
| Synthetic: 2M | 29.736 | 20.609 | 30.69% |

## Import peak RSS

MiB (2^20 bytes): **median [min, max]** of each process peak.
Signed change compares medians; positive means more memory.

| Dataset | Before | After | Change |
|---|---:|---:|---:|
| Real: BPIC12 | 192.1 [187.6, 192.7] | 192.9 [187.2, 195.8] | +0.44% |
| Real: Help Desk | 127.6 [126.9, 128.5] | 128.3 [127.8, 129.8] | +0.61% |
| Synthetic: 100k | 157.9 [157.2, 158.8] | 157.9 [156.7, 159.6] | +0.04% |
| Synthetic: 500k | 283.8 [279.1, 293.8] | 283.9 [282.8, 284.4] | +0.04% |
| Synthetic: 1M | 430.0 [424.3, 438.1] | 426.0 [420.4, 430.1] | -0.92% |
| Synthetic: 2M | 737.5 [728.0, 747.7] | 732.4 [727.1, 748.3] | -0.69% |

Overlapping RSS ranges do not support a meaningful memory-saving claim; the
largest observed 2M peak actually increased slightly, 747.7 to 748.3 MiB.

## Profile -> smallest targeted fix

The expensive import work was Python source parsing/canonical staging, not
the summary query. Import scanned the whole source once to count cases/traces,
then again to validate and stage events. Every timestamp also constructed a
new UTC epoch and explicitly converted to UTC before integer subtraction.

Selected cProfile cumulative seconds (nested timings must not be added;
instrumentation changes absolute timings):

| Work | Before | After |
|---|---:|---:|
| 2M CSV: standalone `csv_trace_count` | 4.370 | no longer called by import |
| 2M CSV: `CsvSource.rows` calls | 4,000,002 | 2,000,001 |
| 2M CSV: `utc_epoch_ms` | 4.526 | 1.810 |
| BPIC12: standalone `source_trace_count` | 3.522 | no longer called by import |
| BPIC12: `_iter_traces` calls / cumulative seconds | 26,176 / 7.158 | 13,088 / 3.644 |

Adapters now return their source count on generator exhaustion; staging
collects it via `yield from`. CSV reuses its existing positions dictionary;
XES retains the count of **all** traces, including empty ones. Existing
standalone count APIs remain available to callers. A shared UTC epoch and
aware-datetime subtraction preserve exact integer millisecond/floor semantics.
Fingerprint checks, validation, staging cleanup and transactional COPY remain.
No query refactor or dependency change was made.

## Correctness, commands and results

At the before revision, then after applying `95164c9`:

```powershell
uv run piw-benchmark measure --label before-current-main
uv run piw-benchmark snapshot --out data/benchmark/before-snapshot.json
uv run piw-benchmark profile synthetic-2000k import --top 35
uv run piw-benchmark profile bpic2012 import --top 25
# Apply the implementation, then:
uv run piw-benchmark measure --label after-single-pass
uv run piw-benchmark profile synthetic-2000k import --top 12
uv run piw-benchmark profile bpic2012 import --top 12
uv run piw-benchmark snapshot --out data/benchmark/after-snapshot.json
uv run piw-benchmark compare --before data/benchmark/before-snapshot.json --after data/benchmark/after-snapshot.json --out evidence/performance/invariant.json
uv run piw-slice0
$env:PIW_REQUIRE_REAL_DATA = "1"
uv run pytest -q
Set-Location web
npm ci
npm run build
npm run test:e2e
Set-Location ..
```

- **Invariant PASS:** `invariant.json` records 42 byte-identical normalized
  answer groups across all six datasets. Normalization uses existing
  `canonical_json(...).encode("utf-8")`; no fields are deleted or tolerances
  applied. Only hashes and booleans are published, not raw responses.
- Coverage per dataset: import report with explicit seven-day SLA, default
  and seven-day `describe_log`; variants and transitions in both orders
  (limit 100); activities in all four orders (limit 100); lexicographically
  first case in raw and complete perspectives. These are fixed regression
  invocations, not an exhaustive proof for every case, limit or parameter.
- `uv run piw-slice0`: PASS, 13,087 cases / 262,200 raw / 164,506 analysis events.
- `PIW_REQUIRE_REAL_DATA=1; uv run pytest -q`: **235 passed**, no skips,
  79.21 s, including real-data PM4Py checks and nine added semantic guards.
  Two upstream warnings: Starlette/AnyIO deprecation and optional PM4Py `r4pm`.
- In `web/`, `npm ci` succeeded; `npm run build` passed TypeScript and Vite.
  First E2E attempt failed because the canonical baseline database was not
  ready yet (`dataset-select` was empty / selected dataset "none available").
  After `uv run piw-slice0` completed, rerunning **only**
  `npm run test:e2e` passed **2/2 in 7.8 s**. No test expectation was relaxed.
- `npm ci` reported the already documented one high-severity
  `source-map-js` advisory; the web lock and dependencies were not changed.

## Limits and remaining scope

Measurements are serial before/after blocks on one workstation, not randomized
interleaving or a confidence-interval study. The 2M baseline has a broad range
(18.546–26.218 s); nevertheless every measured after import run was faster than
every before run. The 38.68% claim is specifically the pre-registered median
comparison, not a universal speedup guarantee.

Queries were intentionally left unchanged, and some measurements worsened:
2M `list_variants` median was 2.067 -> 2.281 s. There is no claim of across-the-
board query improvement. Import remains the largest phase; a second query
optimization was not necessary for the agreed target.

No raw private data was committed. `.mixdog/` is unrelated and left untracked.
No push, PR, merge or deployment was performed. Historical verification/model
evaluation verdicts are not replaced by this performance result.
