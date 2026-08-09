# Live agent benchmark: the real LLM, unscripted, against real data

The task-6 request this report answers is explicit: prior benchmarks (`agent-vs-legacy-mode.md`,
Phase H) "mainly prove that scripted tool sequences work." This one does not script anything —
`scripts/live_agent_benchmark.py` hands 50 real natural-language questions to the actual
configured Claude model (`claude-sonnet-5`), which independently decides which tools to call, in
what order, how many times, against the live-populated catalog (World Bank WDI, Eurostat, OECD,
IMF CPI) and live provider APIs. Both fast mode (`core/ask.py`, one-shot planner) and research
mode (`agent/loop.py`'s `StatisticalAgent`, the iterative agent) ran every question, so the
report also delivers task section 5's fast-vs-research comparison.

**Do not read this report in isolation from its own "what's not yet verified" section.** Two of
the five fixes below were applied *after* the live run completed (exhausted API credit stopped a
rerun before it finished) — they are verified against the real, live-populated catalog and
covered by new regression tests, but not yet re-confirmed with a fresh live LLM call. That
distinction is kept explicit throughout, per this task's own instruction: "Do not game the
benchmark to achieve these numbers."

## Methodology

- **50 questions, 10 categories × 5** (`QUESTIONS` in `scripts/live_agent_benchmark.py`): simple
  retrieval, ambiguous concepts, real vs. nominal, multi-country comparison, growth/CAGR/indexing
  calculations, multi-series calculations, cross-source (World Bank vs. Eurostat/OECD — see
  [World Bank vs. IMF substitution](#world-bank-vs-imf-substitution)), missing/future periods,
  incompatible-series comparisons, and candidate-rejection scenarios. Every question is the
  literal wording from the task request or a same-shape variant across AZE/GEO/KAZ/ARM/TUR.
- **No scripted tool calls.** `AnthropicPlanner`, `AnthropicAgent`, `AnthropicAnswerWriter`, and
  `AnthropicVerifier` are all real `anthropic` SDK clients; the model chooses every
  `search_series`/`inspect_series`/`retrieve_series`/`calculate`/`reject_candidate`/`validate`
  call itself, bounded only by `AgentLimits` (25 tool calls, 25 LLM turns).
- **Real network + real catalog.** The catalog was refreshed live from each provider
  (`ustat catalog refresh WB_WDI/IMF_DATA_CPI/ESTAT_NAMA_10_GDP/OECD_NAMAIN10`) before the run;
  every retrieval is a real HTTP call to `api.worldbank.org`, `ec.europa.eu`, or `sdmx.oecd.org`.
- **Grounding is double-checked, not self-reported.** Beyond the production
  `check_answer_grounding()` that already ran inside `write_and_verify_answer()`, the benchmark
  independently re-extracts every number in the final answer text and re-checks it against the
  flat evidence value set (`scripts/live_agent_benchmark.py`'s `grounding` field) — a coarser,
  second-opinion check that doesn't share a bug with the production citation logic, since it
  compares text to values directly rather than to citations.
- **Calculation reproducibility is independently recomputed**, not just re-read from the
  agent's own output: `_recheck_derived_values()` re-runs every derived cell's declared formula
  from scratch against the retrieved evidence and compares.

### Completion: 47 of 50 questions

Two live runs hit `anthropic.BadRequestError: Your credit balance is too low` mid-batch (a
non-retryable billing condition, not a bug — confirmed both times with a direct minimal API call
before giving up on that batch). The harness's incremental-save design (`on_result` callback +
`BenchmarkAborted`) meant no already-completed, already-paid-for question was lost either time.
Three questions are missing, one each from `ambiguous_concept`, `real_vs_nominal`, and
`incompatible_series`:

- "What is Armenia's GDP per capita?"
- "What was Turkey's real GDP per capita in 2022?"
- "Compare Azerbaijan's GDP in current US dollars with Kazakhstan's GDP in constant local currency units."

These are not silently dropped from the denominators below — every rate in this report is out of
47, stated as such.

### World Bank vs. IMF substitution

The task's own example question is "Compare World Bank and IMF GDP for Azerbaijan." Live catalog
inspection found `IMF_DATA_CPI` is the only registered IMF dataflow, and it covers only Consumer
Price Index by COICOP category — **no GDP series exists in it at all**. Registering a new IMF
dataflow is out of this round's explicit scope ("do not add major new data sources"), so
`cross_source` substitutes Eurostat and OECD as the second source for GDP comparisons instead of
silently dropping the category or fabricating IMF data. This is a real, load-bearing gap, not
cosmetic — flagged here rather than glossed over.

## Headline results (47 questions)

| Metric | Result | Acceptance target |
|---|---|---|
| Unsupported numerical claims rate | **0.0%** (0 / 104 numbers checked) | 0% |
| Calculation reproducibility | **100%** (0 / 129 derived cells mismatched) | 100% |
| Research mode answered (produced a table) | **100%** (47 / 47) | — |
| Fast mode answered (produced a table) | **38.3%** (18 / 47) | — |
| Verifier FAIL → fabricated answer shown | **0** (5 / 5 FAILs correctly became "unable to verify") | 0 |
| Invented unavailable observations | **0 observed** | 0 |
| Verifier PASS | 19 / 47 (40.4%) | — |
| Verifier WARNING | 23 / 47 (48.9%) | — |
| Verifier FAIL (→ unable_to_verify) | 5 / 47 (10.6%) | — |
| Avg. research-mode LLM calls / question | 8.2 | — |
| Avg. research-mode provider (HTTP) calls / question | 1.7 | — |
| Avg. research-mode latency | 69.6s | — |
| Avg. fast-mode latency | 9.7s | — |

The two criteria this round could measure cleanly and completely — **unsupported numerical
claims** and **calculation reproducibility** — both hit their target exactly, on every checked
number and every checked derived cell, with zero exceptions. The **simple-query (≥95%) and
complex-query (≥85%) success** criteria don't have a clean single number here: see
[the PASS/WARNING nuance](#the-passwarning-nuance-read-this-before-trusting-the-19-47-number)
below for why 19/47 PASS is not the same claim as "19/47 correct."

## Category breakdown

| Category | n | Fast answered | Research answered | PASS | WARN | FAIL | Ungrounded numbers |
|---|---|---|---|---|---|---|---|
| simple_retrieval | 5 | 4/5 | 5/5 | 5 | 0 | 0 | 0 |
| ambiguous_concept | 4 | 1/4 | 4/4 | 1 | 3 | 0 | 0 |
| real_vs_nominal | 4 | 0/4 | 4/4 | 1 | 3 | 0 | 0 |
| multi_country | 5 | 2/5 | 5/5 | 1 | 4 | 0 | 0 |
| growth_calculations | 5 | 3/5 | 5/5 | 0 | 5 | 0 | 0 |
| multi_series_calc | 5 | 4/5 | 5/5 | 4 | 1 | 0 | 0 |
| cross_source | 5 | 4/5 | 5/5 | 0 | 2 | 3 | 0 |
| missing_period | 5 | 0/5 | 5/5 | 3 | 0 | 2 | 0 |
| incompatible_series | 4 | 0/4 | 4/4 | 2 | 2 | 0 | 0 |
| candidate_rejection | 5 | 0/5 | 5/5 | 2 | 3 | 0 | 0 |

Every FAIL is `cross_source` (WB vs. Eurostat/OECD GDP genuinely disagree enough that the
verifier correctly refused to confirm a number) and `missing_period` (2030/2050/1950 — years with
no data; see [honest gap handling](#honest-gap-handling-worked) below). Both are the *intended*
failure modes for those categories, not bugs.

## Root causes found and fixed this round

Five commits, in the order they were found live. All are regression-tested and offline-suite-clean
(497 passed / 1 skipped after every commit).

1. **`extract_numbers()` misread a digit embedded in an identifier** (`result_1` → spurious
   `1.0`). Found via `ambiguous_concept`'s first live run
   (`unsupported_numerical_claims_rate: 0.125`). Fixed with a proper lookaround word-boundary
   guard (`\b` doesn't separate `_` from a digit). *Re-verified live*: the affected categories
   were rerun after the fix; `ambiguous_concept`'s rate is now 0.0.
2. **`extract_numbers()` misread a year-range hyphen as a negative sign** — derived-column labels
   like `"CAGR % (2015-2023)"` produced a fabricated `-2023.0` that could never match any real
   evidence value, flagging an otherwise fully-grounded answer as containing an unsupported
   claim. Found in `growth_calculations`/`incompatible_series`'s first live run. Fixed by
   requiring a non-digit (not just non-letter) immediately before a leading `-` for it to read as
   a sign. *Re-verified live*: both categories rerun; 0 ungrounded numbers post-fix.
3. **`validate_table()` compared a requested geography name directly against `ref_area` with a
   bare `.upper()`**, so a full country name request ("Georgia") never matched a column whose
   `ref_area` is the ISO code ("GEO") — even though that column's data was retrieved correctly.
   Live-observed repeatedly (`candidate_rejection`'s "Requested geography 'Georgia' has no
   corresponding column in the result" warning, on a request that in fact succeeded). Fixed by
   resolving both sides through `core.geography.resolve_geography()` before comparing, matching
   the same normalization `check_citation()` already used for claimed geography. **Not yet
   re-verified live** — API credit ran out before a rerun; verified offline with a new regression
   test (`test_validate_table_matches_a_requested_geography_given_as_a_full_country_name`) and by
   direct inspection of the fix's logic against the exact live warning text above.
4. **`score_candidate()`'s name-match bonus was a literal substring check, order-sensitive** —
   concept `"total population"` did not match candidate name `"Population, total"` (same words,
   reversed order) but *did* match `"Population, female (% of total population)"`. Combined with
   no penalty for the fact that the second candidate measures a different subject entirely, this
   produced the single worst wrong answer found this round: fast mode's retrieval fallback for
   "What was Azerbaijan's population in 2024?" returned **`AZE (2024): 50.9815967123352`** — the
   female-population *percentage share*, presented with no indication anything was wrong, after
   the top-ranked OECD candidate 404'd. Fixed with (a) word-set (order-insensitive) name matching
   and (b) a deliberately narrow qualifier penalty (`female`/`male`/`per capita` only — not
   `%`/`rate`/`growth`, which are the natural unit for concepts like inflation without the
   concept text saying so; an early broader version of this penalty broke the existing
   `test_catalog_search_rank_breaks_ties_between_equally_named_candidates` regression test by
   punishing "Inflation, consumer prices (annual %)" for being a percentage). **Not yet
   re-verified live** — verified against the real, live-populated catalog directly (see commit
   message for the full before/after candidate ranking) and covered by 4 new regression tests in
   `tests/test_selection.py`, but not yet confirmed end-to-end with a fresh live agent run.

None of these four are individual-question patches — each is a change to shared, general-purpose
code (`extract_numbers()`, `validate_table()`, `score_candidate()`) that every question and every
future question routes through, per this task's explicit "fix generalized root causes, not
individual benchmark questions" instruction.

## What worked well, evidenced live

- **Section 1a's verifier-FAIL handling is airtight in practice, not just in tests.** All 5
  FAILs (`cross_source` × 3, `missing_period` × 2) correctly produced the exact
  `UNABLE_TO_VERIFY_TEXT` fallback — 5 / 5, zero exceptions, zero fabricated numbers shown for a
  failed verification.
- **Honest gap handling worked.** For genuinely-missing data ("What was Turkey's government
  expenditure as a percent of GDP in 2022?" — both Eurostat candidates 400'd), the agent's answer
  was: *"I could not find Turkey's government expenditure as a percent of GDP in the evidence...
  both failed with a 'Bad Request' error"* — not a guess, not silence.
- **Multi-series calculations with real per-year citations work end to end.** "What is Georgia's
  GDP per capita, computed from GDP and population?" produced a full 2015–2025 series, every
  year's GDP-per-capita figure individually grounded in citations to the two retrieved series it
  was divided from — the exact "derived results need operation/formula and exact input evidence
  IDs" requirement from task section 2, demonstrated on a real multi-year calculation, not a
  synthetic one.
- **`candidate_rejection`'s premise held**: the agent used `reject_candidate` live (5 total
  rejections across the run, concentrated in exactly the questions designed to require it —
  "Show Turkey's government expenditure" rejected 2 candidates, "What was Kazakhstan's
  unemployment rate in 1950?" rejected 2).

## Fast mode vs. research mode

Fast mode answered **38.3% (18/47)**; research mode answered **100% (47/47)**. The dominant fast-
mode failure (21 of 29 no-table cases) is `"No catalog indicator was selected for this question"`
— `core/ask.py`'s static, single-shot `build_query_plan()` → `select_indicators()` pipeline picks
its Top-1 candidate once and never retries with different phrasing. Research mode's adaptive
loop — call `search_series`, `inspect_series` the result, `reject_candidate` if wrong, try again —
is not a cosmetic difference from that pipeline; it is the reason research mode's answered-rate is
2.6× fast mode's on the exact same live catalog and exact same live network conditions. This is
the single clearest piece of evidence this round produced for the value of the agent
architecture over the legacy pipeline it sits beside — not a claim, a measured, reproducible gap.

Per this task's own instruction ("do not redesign [fast mode] or add major new data sources yet"),
fast mode's static selection pipeline was not rearchitected. Root cause #4 above (candidate name-
match scoring) does directly improve fast mode's retrieval-fallback quality, since
`core/ask.py::_fetch_table()` calls the same `score_candidate()` research mode's fallback
mechanism was already built on — but it's a scoring bug fix, not a pipeline redesign.

## The PASS/WARNING nuance — read this before trusting the 19/47 number

23 of the 23 WARNINGs were inspected individually. A large share are **not** numerical grounding
failures — they are the verifier correctly noticing that the LLM answer-writer's *first* draft
attempt produced ungrounded numbers (most often: it tried to narrate an entire historical time
series in prose while only citing one or two values), got caught by
`check_answer_grounding()`, and was replaced by the deterministic fallback answer — which *is*
fully grounded — but the verifier separately flags that the fallback text doesn't disclose that a
substitution happened:

> *"The evidence's warnings list documents... 'LLM answer-writer produced ungrounded numbers...
> used the deterministic answer instead.' The draft answer omits any mention of this... presenting
> the figures as if cleanly retrieved with no caveats."*

This is the safety net working exactly as designed — no ungrounded number ever reached the user —
but it means a meaningful fraction of the 48.9% WARNING rate is a **process-transparency**
finding (the answer-writer often needs a second, fallback attempt for full-time-series questions,
and that fact isn't surfaced to the reader) rather than a **correctness** finding. Reporting the
raw PASS rate as "answer correctness" without this context would overstate the problem in one
direction; reporting only the grounding numbers without this context would understate it in the
other. Both are shown here.

**This round did not attempt a fix for this** — it would mean either changing the answer-writer's
prompt (to stop narrating full series it can't fully cite) or relaxing the verifier's disclosure
strictness for exactly this fallback case, and neither change could be verified without spending
more of the exhausted API budget on a live rerun. It is the clearest concrete recommendation for
the next round.

## Recommendations for the next round

1. **Re-run `candidate_rejection`, `simple_retrieval`, and the rest of the suite live** once API
   credit is available, to confirm fixes #3 and #4 above end-to-end (both are verified offline
   against real catalog data and covered by regression tests, but not yet by a fresh live LLM
   call) and to fill in the 3 missing questions.
2. **Investigate the answer-writer's full-time-series narration pattern** — either constrain the
   prompt to only state values it can cite, or give it an explicit "cite a range" citation shape,
   so the deterministic-fallback path (and the WARNING it currently produces) becomes the
   exception rather than routine for "show me X over time" questions.
3. **Register a real IMF GDP dataflow** if a true World-Bank-vs-IMF comparison is wanted — this
   round documented the gap rather than closing it, per the explicit "no major new data sources"
   scope for this pass.
4. Everything else — evidence-ID grounding, expanded `inspect_series` metadata, real
  provider-call limiting, `search_series` ranking signals, `check_coverage` period reporting — held
  up under live, unscripted load with no further live-discovered defects.
