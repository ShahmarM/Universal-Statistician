# Phase H: 100-question real end-to-end benchmark

Honest record of running 110 natural-language-style questions through the
real `answer_question()` pipeline (`core/ask.py`) against the real,
live-populated catalog (38,789 indicators) and real live retrieval from
official APIs — World Bank, Eurostat, US Census — no fake providers, no
synthetic data. Script: `scripts/benchmark_end_to_end.py`.

## Honest scope limit: interpretation accuracy not measured this round

Each question's `QuestionInterpretation` is hand-authored ground truth
(`ScriptedPlanner`, mirroring `tests/test_benchmarks.py`'s existing
pattern), not produced by a live LLM call. This sandbox's shell state does
not persist an exported environment variable across separate tool
invocations, so the `ANTHROPIC_API_KEY` pasted in an earlier round of this
project was only ever live for that one call — there is no key currently
available to this script. **Interpretation accuracy (raw question text ->
structured plan) is therefore not measured here.** Phase G already
exercised that step end to end with a real `AnthropicPlanner` (against
synthetic data, since live retrieval wasn't available yet — see
`docs/benchmarks/phase-g-live-planner-report.md`). This phase inverts
that: live retrieval, scripted interpretation — measuring everything
downstream of a *correct* interpretation: catalog selection, retrieval,
transformation calculation, provenance, and validation.

## Question set

110 questions across 7 categories, generated from a pool of concepts
(reusing Phase C's verified 54-concept ground truth where applicable) and
20 countries:

| Category | Count | Tests |
|---|---|---|
| `direct` | 60 | single country, single concept, latest value |
| `time_series` | 10 | single country/concept, explicit 2015-2023 range |
| `comparison` | 10 | one concept across 3 countries |
| `cross_indicator` | 5 | 3 concepts for one country |
| `transformation` | 10 | growth / cumulative_growth / rank |
| `hard_selection` | 10 | concepts Phase C already found ranked poorly (exports, imports, government expenditure, industrial production, poverty rate) — included deliberately, not cherry-picked out, so the benchmark measures real accuracy rather than only easy cases |
| `cross_source` | 5 | Eurostat GDP (`gross domestic product at market prices`) for 5 EU countries |

## Results

First run (no request pacing, no retry): **31.8% end-to-end success**
(35/110) — almost every failure was `502 Bad Gateway` from World Bank's
live SDMX endpoint, which does not tolerate ~100 sequential requests with
no pacing. This is a real, live-discovered production-readiness gap (see
"Bugs found and fixed" below), fixed in two steps, then re-run:

| Run | Retrieval success | End-to-end success |
|---|---|---|
| No pacing, no retry | 35/110 (31.8%) | 35/110 (31.8%) |
| + retry-with-backoff | 82/110 (74.5%) | 82/110 (74.5%) |
| + request pacing (0.5s) | **103/110 (93.6%)** | **103/110 (93.6%)** |

Final run, full metrics:

| Metric | Result |
|---|---|
| Retrieval success | 103/110 (93.6%) |
| Top-1 indicator rate | 95/105 (90.5%) |
| Calculation correctness | 10/10 (100.0%) |
| Provenance present | 101/110 (91.8%) |
| Validation PASS / WARNING / FAIL / NONE | 87 / 16 / 0 / 7 |
| End-to-end success | **103/110 (93.6%)** |

By category:

| Category | Success |
|---|---|
| comparison | 10/10 |
| cross_indicator | 5/5 |
| cross_source | 5/5 |
| direct | 59/60 |
| hard_selection | 4/10 |
| time_series | 10/10 |
| transformation | 10/10 |

**Calculation correctness** is verified by recomputing each derived
column from the *same table's own* raw retrieved values (e.g. `growth% =
(end - start) / start * 100`, `rank` is a valid permutation) rather than
against an external golden number — both input and output came from one
live retrieval, so this proves the transformation math is right without
needing to guess or hardcode an expected figure that would go stale.

## Manual verification against official sources

Per the task's requirement to manually verify a sample against official
sources: 5 values retrieved through the real pipeline were independently
re-fetched via World Bank's classic REST API (`api.worldbank.org/v2/...`,
a completely separate code path from the `sdmx1`-based provider this
project uses) and compared:

| Indicator | Country | 2022 value (our pipeline) | 2022 value (independent WB REST fetch) |
|---|---|---|---|
| GDP (current US$) | USA | 26,054,614,000,000 | 26,054,614,000,000 |
| Population, total | DEU | 83,177,813 | 83,177,813 |
| Inflation, consumer prices (annual %) | FRA | 5.22236748369725 | 5.22236748369725 |
| Unemployment, total (% of labor force) | JPN | 2.614 | 2.614 |
| Life expectancy at birth (years) | GBR | 81.0112195121951 | 81.0112195121951 |

All 5 match exactly.

## Bugs found and fixed this round

1. **World Bank's SDMX endpoint returns 502s under an unpaced burst of
   requests.** `SDMXProvider` had no retry logic at all — a transient
   upstream failure permanently failed that call. Fixed with a small
   retry (3 attempts, 1s/2s backoff) for 502/503/504 and
   `ConnectionError` specifically (`providers/sdmx_provider.py`); a real,
   non-transient error like 404 still fails immediately. This alone
   raised end-to-end success from 31.8% to 74.5%. The remaining gap
   needed the benchmark itself to pace its own requests (0.5s between
   questions) — retry survives brief blips, not sustained rate limiting;
   pacing is the other, complementary half of being a well-behaved API
   consumer. (Relevant to Phase K's upstream-timeouts/rate-limiting scope
   too, from the client side.)
2. **Indicator selection could pick a noise candidate over the flagship
   one on a tie.** Asking for "inflation" (USA) selected a US Census
   income-adjustment variable instead of WB's CPI indicator.
   `core/selection.py::score_candidate()` scored every candidate from
   catalog metadata alone (name substring, geographic coverage,
   frequency) and discarded `Catalog.search()`'s own ranking (Phase C)
   entirely. Both candidates' names contained "inflation" (one as the
   real concept, one as the unrelated compound "inflation-*adjusted*"),
   and neither had `geographic_coverage` data to differentiate on, so the
   score tied exactly and fell through to an arbitrary
   `(source_id, indicator_id)` sort — which happened to prefer
   `"US_CENSUS_ACS1"` over `"WB_WDI"` alphabetically. Fixed by threading
   each candidate's original catalog search rank through
   (`CandidateIndicator.search_rank`) and factoring it into
   `score_candidate()`, so selection stays consistent with Phase C's
   already-tuned ranking instead of re-deriving a weaker signal from
   scratch. Both fixes are covered by new offline regression tests
   (`tests/test_sdmx_provider.py`, `tests/test_selection.py`) — 295
   offline tests now (was 290 before this phase).

## What's still honestly missing (documented, not fixed this round)

The 7 remaining failures, all understood and traced to their exact cause:

- **1 `direct` failure**: `government debt` for Mexico correctly selected
  the Eurostat indicator (`ESTAT_NAMA_10_GDP/GD`, a legitimate member of
  that concept's acceptable family per Phase C), but Eurostat's own API
  returned `400 Bad Request` — Mexico isn't actually covered by that
  dataflow's government-debt series, a genuine dataset-coverage gap, not
  a bug in this project's code.
- **6 `hard_selection` failures**: these are the Phase C-documented
  cross-source ambiguity and stemming gaps, now shown causing real
  downstream retrieval failures, not just a lower search rank — e.g.
  "industrial production" for GBR/POL selected a US Census *occupation
  category* ("Industrial production managers", a headcount of a job
  title, not an economic index), which then fails outright for a non-US
  country. "exports"/"imports"/"government expenditure" selected
  Eurostat's *classification-code* entries (`P61`, `P71`, `TE`), which
  are valid dimension values in that dataflow's codelist but return `400
  Bad Request` when actually queried for some country/code combinations
  — a discovered-but-not-actually-populated combination, distinct from
  Phase C's ranking problem and not previously visible from search alone.

These are the same honestly-scoped gaps Phase C already named (stemming,
cross-source ambiguity) manifesting one layer further downstream, at
retrieval instead of just ranking — consistent with this project's
practice of measuring and reporting real failure modes rather than
narrowing the benchmark to avoid finding them.
