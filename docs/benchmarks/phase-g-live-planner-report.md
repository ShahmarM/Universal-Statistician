# Phase G: real end-to-end NL test with a live AnthropicPlanner

This is the honest record of running `AnthropicPlanner` (model
`claude-sonnet-5`) with a real Anthropic API key against the task
description's own 10 example questions, per the "real end-to-end natural
language test" stage.

## Scope and honest limits

- **The Anthropic API is reachable from this sandbox** (`anthropic.com` is
  allowed by the network policy, unlike every official statistics API) —
  confirmed with a live call before anything else, not assumed.
- **The official statistics APIs are still blocked** (Phase A). So this
  phase could not run the questions against real official retrieval — only
  question → LLM interpretation → query plan → catalog search → selection →
  transformation dispatch → validation → answer, against a small,
  clearly-synthetic multi-country dataset built for this run (`AZE`/`GEO`/
  `KAZ` population, GDP, GDP per capita, inflation, unemployment — not real
  values, the same "clearly synthetic, not live data" standard
  `tests/test_benchmarks.py` already uses). That is still the part Phase G
  actually cares about: does a real model's output work with this system's
  structural code, not "is World Bank's number for Azerbaijan's population
  correct" (a Phase H concern, once live retrieval is possible).
- The API key used for this run was provided by the user directly in chat
  (not set as environment/session configuration) — used only for this
  session, never written to any file in the repository or committed.

## What broke on the first live run (before any fix)

All 10 questions were run once, unmodified, against the existing code.
**0 of 10 produced a data table.** The two real, generalized causes:

1. **Geography names, not codes.** The model returned `geographies:
   ["Azerbaijan"]`, not `["AZE"]` — reasonable, since the planner's own
   system prompt explicitly told it either form was acceptable (section 11:
   "in whatever form the user used them"). Every provider's `ref_area`
   needs a code, not a name, so retrieval failed for every question that
   got far enough to attempt it (`KeyError`-shaped failures in the test
   harness; a live provider would return an empty/malformed result for the
   same reason).
2. **The literal string `"latest"` where a period was left unspecified.**
   For "...to the latest available year", the model wrote
   `end_period: "latest"` instead of `null`. The system's existing
   "resolve to latest available" idiom (`end_period or periods[-1]`,
   already used throughout `compose.py`) depends on the field being falsy
   when unspecified — the string `"latest"` is truthy, so it silently
   defeated that idiom rather than triggering it.

Both are structural gaps, not one-off prompt-wording accidents — every one
of the 10 questions that mentioned a country name hit gap #1, and 2 of 10
hit gap #2.

## Fixes (structural, not prompt-only)

Per this phase's own instruction ("Do not simply tweak the LLM prompt...
Fix generalized problems"), both were fixed in code, with prompt/schema
wording strengthened only as defense-in-depth on top:

- **`core/geography.py`** (new): `resolve_geography()` converts a country
  name or any ISO form to canonical alpha-3 via `pycountry` (the same
  "don't write your own X" principle already applied to `sdmx1`/`pxwebpy`
  for their protocols) — never raises, returns input unchanged if
  unresolvable. `provider_ref_area()` converts that canonical form to
  whatever code a *specific* source's `ref_area` actually expects (World
  Bank/IMF: alpha-3; Eurostat: alpha-2, per `registry.py`'s own worked
  examples). `build_query_plan()` now resolves every geography (and
  `weighted_average`'s `inputs`, which are geography codes too) once,
  before selection/retrieval; `core/ask.py::_fetch_table()` applies the
  source-specific conversion only at the retrieval call itself, so column
  keys/validation/chart labels stay on the canonical form regardless of
  which source served a given geography.
- **`core/query_plan.py::_clean_period()`** (new): treats
  `"latest"`/`"present"`/`"now"`/`"current"`/`"today"` (case-insensitively)
  as `None` when parsing `start_period`/`end_period`/`base_period` from any
  planner's raw output — applied in `QuestionInterpretation.from_dict()`
  and `TransformationSpec.from_dict()`, so it protects `/plan` and `/ask`
  API requests too, not just this planner.
- `AnthropicPlanner`'s system prompt and JSON schema field descriptions
  were also strengthened (prefer ISO alpha-3 codes; never write "latest" as
  a period value) — belt-and-suspenders, not the fix itself.

## Result after the fix: same 10 questions, real model, re-run

**4 of 10 now produce a full table** (was 0 of 10) — verified against the
actual retrieved/computed numbers, not just "didn't crash":

| # | Question | Result |
|---|---|---|
| 1 | Population of Azerbaijan | ✅ table, `AFG`→`AZE` resolved automatically |
| 3 | GDP per capita: AZE/GEO/KAZ | ✅ comparison table, all three countries resolved |
| 6 | Index Azerbaijan GDP to 2015=100 | ✅ `AZE__index`, verified against the underlying series: `62.3 / 46.4 * 100 ≈ 134.3`, matches the returned `134.27` |
| 10 | Latest GDP per capita for Azerbaijan | ✅ `"AZE (2024): 8300.0"` — correctly the latest year (2024, not 2023) |

The other 6 are **not** retrieval/geography failures — every one is either
a genuinely out-of-scope case or a catalog-search miss on this run's
deliberately thin synthetic catalog (5 concepts, not the real ~1,500-entry
WDI catalog Phase B's discovery would populate):

- **Q7 ("Which countries have the highest unemployment rate?")**: the
  model correctly left `geographies: []` rather than guessing a country
  list for a genuinely open-ended question — the system honestly reports
  "no geography identified" instead of fabricating a "top N" answer. This
  is the anti-hallucination design working as intended, not a bug; the
  system currently has no mechanism for open-ended "globally, which
  countries..." questions at all, an honest, explicit boundary, not
  something patched in this phase.
- **Q2, Q4, Q5, Q8, Q9 ("nominal GDP", "inflation rate (consumer prices)",
  "real GDP growth", "GDP growth")**: catalog search found nothing for
  these exact phrases against this run's minimal seed catalog (5 entries
  with plain names like "GDP (current US\$)", not "nominal GDP"). This is
  squarely Phase C's territory (catalog search quality) — explicitly
  deferred by the user pending real discovered data, because a search
  benchmark run against 4-5 hand-written entries (or this run's slightly
  larger 6-entry fixture) isn't representative of the real, much richer
  catalog Phase B's discovery would build. Not fixed here; noted honestly
  as the same open item, not silently different from Phase C's deferral.

## One further, real observation (not fixed, documented instead)

For Q6 ("Index Azerbaijan GDP to 2015 = 100"), the bare concept `"GDP"`
matched three catalog candidates in the synthetic fixture: nominal GDP,
real (constant-price) GDP, and GDP per capita — all three legitimately
contain "GDP" in their name. `core/selection.py`'s scoring gave nominal and
real GDP an equal score (both had a name match and Phase F's "semantics
documented" bonus); the deterministic tie-break (alphabetical by
`indicator_id`) happened to pick nominal GDP, a reasonable default, but
*by alphabetical accident*, not a deliberate "prefer nominal for a bare
'GDP' concept" policy. This is deterministic and explainable (never
silently mixes candidates), so it is not a correctness bug — but it is a
real design gap worth flagging rather than quietly accepted: a future
phase could give selection an explicit, stated default price-basis
preference (e.g. nominal) so the outcome is a *decision*, not a
coincidence of indicator-id spelling.

## Regression tests

- `tests/test_geography.py` (new): `resolve_geography()`/
  `provider_ref_area()` unit tests.
- `tests/test_query_plan.py`: `build_query_plan()` resolves country names
  and `weighted_average` geography inputs; `QuestionInterpretation.
  from_dict()`/`TransformationSpec.from_dict()` clean `"latest"`-shaped
  period words.
- `tests/test_ask.py`: an end-to-end test using the exact shape a live
  planner produced (`geographies=("Afghanistan",)`, a country name, not a
  code) proving retrieval now succeeds — not a hypothetical scenario, the
  literal thing that failed on the first live run.

**279 offline tests passing** (was 266) — all still pass with zero live
API access; the live run itself is not part of the automated suite (no
`ANTHROPIC_API_KEY` in CI), consistent with how `-m network` tests are
handled throughout this project.
