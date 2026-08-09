# Agent (research mode) vs. legacy (fast mode): 50-question benchmark

Task section 20's benchmark, run via `scripts/benchmark_agent_vs_legacy.py`
against the offline fixture that script builds. **Do not remove fast mode
based on this report** — see [Recommendation](#recommendation).

## Methodology, stated honestly up front

This sandbox has no `ANTHROPIC_API_KEY` and no network egress to
`api.anthropic.com` (the same constraint every earlier live-LLM phase in
this project — G, H, I, J — documented explicitly). Neither mode's LLM
step is a real model call here. Instead:

- **Fast mode** gets a `QuestionInterpretation` scripted the way a naive,
  no-real-NLU planner would produce it — deliberately reproducing
  `RuleBasedPlanner`'s limits (bare concept extraction, no transformation
  detection), since real natural-language interpretation quality isn't
  what this benchmark measures.
- **Research mode** gets a fixed sequence of tool calls scripted the way a
  *competent* investigator should behave for that question — search,
  inspect, reject when wrong, retrieve, calculate, validate.

Both run through the real production code — `core/ask.py::answer_question`,
`agent/loop.py::StatisticalAgent`, `agent/tools.py`, `agent/expressions.py`,
`core/validation.py` — only the two LLM decision points are replaced with
scripts. This is a deliberate choice, not a shortcut forced by the sandbox:
comparing "fast mode with no LLM" against "research mode with a real Claude"
would show research mode winning for the trivial reason that it has a model
and fast mode doesn't, which proves nothing about the *architecture* this
migration actually changed. Holding NLU quality equal (and equally limited)
on both sides isolates the real question — given the same understanding of
the question, does one-shot Top-1 selection or iterative LLM-guided
investigation produce the structurally better result?

**What this does NOT measure:** real model quality (whether an actual Claude
call would produce the scripted tool sequence, a better one, or a worse
one), real natural-language interpretation accuracy end to end, or
latency/cost against a live API. See [Live follow-up](#live-follow-up) for
what running this with a real key would add.

**Regression protection:** `tests/test_agent_benchmark.py` hardcodes a
smaller (2-country) version of the same five scenarios as permanent,
CI-covered tests — the pattern `tests/test_search_quality.py` already
established for `scripts/benchmark_catalog_search.py`'s live-catalog
findings. A future change that silently erodes one of these capability
differences fails a red test, not just a lower number in this file.

## Question set

50 questions: 5 categories × 10 countries (AZE, GEO, KAZ, ARM, TUR, UZB, MDA,
BLR, UKR, RUS — the same ten, unmodified, in every category; not
cherry-picked per question). Each category isolates one specific capability
gap task section 5's Definition of Done and section 20 both describe.

| # | Category | Capability gap | Example question |
|---|---|---|---|
| 1 | Simple direct lookup | none (parity expected) | "What was AZE's population in 2023?" |
| 2 | Price-basis ambiguity | candidate ambiguity resolution | "What was AZE's real GDP growth?" |
| 3 | Multi-source discrepancy | cross-source comparison | "Why do World Bank and IMF GDP figures for AZE differ?" |
| 4 | Independently-sourced share | multi-series calculation | "What share of AZE's GDP is government expenditure?" |
| 5 | Seasonal-adjustment ambiguity | candidate ambiguity resolution | "What is AZE's seasonally adjusted unemployment rate?" |

"Correct" below means the answer's underlying table actually reflects what
the question asked for (the real-price GDP series for a real-GDP question,
both sources for a discrepancy question, a computed share for a share
question) — not merely "some table came back." A wrong-but-present result
looks identical to a right one under a weaker "did it answer at all" metric,
so this benchmark deliberately checks the stronger claim.

## Summary results

| Category | Capability gap | Fast: correct | Research: correct | Fast: avg sources | Research: avg sources | Research: avg tool calls |
|---|---|---|---|---|---|---|
| Simple direct lookup | none | **10/10** | **10/10** | 1.0 | 1.0 | 2.0 |
| Price-basis ambiguity | ambiguity resolution | **0/10** | **10/10** | 1.0 | 1.0 | 7.0 |
| Multi-source discrepancy | cross-source comparison | **0/10** | **10/10** | 1.0 | 2.0 | 6.0 |
| Independent share | multi-series calculation | **0/10** | **10/10** | 0.0 | 1.0 | 6.0 |
| Seasonal-adjustment ambiguity | ambiguity resolution | **0/10** | **10/10** | 1.0 | 1.0 | 6.0 |
| **Total** | | **10/50** | **50/50** | | | |

Reproduce with:

```bash
python scripts/benchmark_agent_vs_legacy.py --json /tmp/results.json
```

## Full per-question results

<details>
<summary>All 50 questions (click to expand)</summary>

### Simple direct lookup (parity — both modes correct on all 10)

| Country | Question | Fast correct | Fast sources | Research correct | Research sources | Research tool calls |
|---|---|---|---|---|---|---|
| AZE | What was AZE's population in 2023? | YES | 1 | YES | 1 | 2 |
| GEO | What was GEO's population in 2023? | YES | 1 | YES | 1 | 2 |
| KAZ | What was KAZ's population in 2023? | YES | 1 | YES | 1 | 2 |
| ARM | What was ARM's population in 2023? | YES | 1 | YES | 1 | 2 |
| TUR | What was TUR's population in 2023? | YES | 1 | YES | 1 | 2 |
| UZB | What was UZB's population in 2023? | YES | 1 | YES | 1 | 2 |
| MDA | What was MDA's population in 2023? | YES | 1 | YES | 1 | 2 |
| BLR | What was BLR's population in 2023? | YES | 1 | YES | 1 | 2 |
| UKR | What was UKR's population in 2023? | YES | 1 | YES | 1 | 2 |
| RUS | What was RUS's population in 2023? | YES | 1 | YES | 1 | 2 |

### Price-basis ambiguity (fast mode wrong on all 10 — picks nominal GDP for a "real GDP" question)

| Country | Question | Fast correct | Research correct | Research tool calls |
|---|---|---|---|---|
| AZE | What was AZE's real GDP growth? | no | YES | 7 |
| GEO | What was GEO's real GDP growth? | no | YES | 7 |
| KAZ | What was KAZ's real GDP growth? | no | YES | 7 |
| ARM | What was ARM's real GDP growth? | no | YES | 7 |
| TUR | What was TUR's real GDP growth? | no | YES | 7 |
| UZB | What was UZB's real GDP growth? | no | YES | 7 |
| MDA | What was MDA's real GDP growth? | no | YES | 7 |
| BLR | What was BLR's real GDP growth? | no | YES | 7 |
| UKR | What was UKR's real GDP growth? | no | YES | 7 |
| RUS | What was RUS's real GDP growth? | no | YES | 7 |

### Multi-source discrepancy (fast mode structurally limited to 1 source on all 10; research retrieves and compares both)

| Country | Question | Fast correct | Fast sources | Research correct | Research sources | Research tool calls |
|---|---|---|---|---|---|---|
| AZE | Why do World Bank and IMF GDP figures for AZE differ? | no | 1 | YES | 2 | 6 |
| GEO | Why do World Bank and IMF GDP figures for GEO differ? | no | 1 | YES | 2 | 6 |
| KAZ | Why do World Bank and IMF GDP figures for KAZ differ? | no | 1 | YES | 2 | 6 |
| ARM | Why do World Bank and IMF GDP figures for ARM differ? | no | 1 | YES | 2 | 6 |
| TUR | Why do World Bank and IMF GDP figures for TUR differ? | no | 1 | YES | 2 | 6 |
| UZB | Why do World Bank and IMF GDP figures for UZB differ? | no | 1 | YES | 2 | 6 |
| MDA | Why do World Bank and IMF GDP figures for MDA differ? | no | 1 | YES | 2 | 6 |
| BLR | Why do World Bank and IMF GDP figures for BLR differ? | no | 1 | YES | 2 | 6 |
| UKR | Why do World Bank and IMF GDP figures for UKR differ? | no | 1 | YES | 2 | 6 |
| RUS | Why do World Bank and IMF GDP figures for RUS differ? | no | 1 | YES | 2 | 6 |

### Independently-sourced share (fast mode produces no table at all on all 10 — no `transformations` extracted from freeform text)

| Country | Question | Fast correct | Fast sources | Research correct | Research tool calls |
|---|---|---|---|---|---|
| AZE | What share of AZE's GDP is government expenditure? | no | 0 | YES | 6 |
| GEO | What share of GEO's GDP is government expenditure? | no | 0 | YES | 6 |
| KAZ | What share of KAZ's GDP is government expenditure? | no | 0 | YES | 6 |
| ARM | What share of ARM's GDP is government expenditure? | no | 0 | YES | 6 |
| TUR | What share of TUR's GDP is government expenditure? | no | 0 | YES | 6 |
| UZB | What share of UZB's GDP is government expenditure? | no | 0 | YES | 6 |
| MDA | What share of MDA's GDP is government expenditure? | no | 0 | YES | 6 |
| BLR | What share of BLR's GDP is government expenditure? | no | 0 | YES | 6 |
| UKR | What share of UKR's GDP is government expenditure? | no | 0 | YES | 6 |
| RUS | What share of RUS's GDP is government expenditure? | no | 0 | YES | 6 |

### Seasonal-adjustment ambiguity (fast mode wrong on all 10 — picks the unadjusted series)

| Country | Question | Fast correct | Research correct | Research tool calls |
|---|---|---|---|---|
| AZE | What is AZE's seasonally adjusted unemployment rate? | no | YES | 6 |
| GEO | What is GEO's seasonally adjusted unemployment rate? | no | YES | 6 |
| KAZ | What is KAZ's seasonally adjusted unemployment rate? | no | YES | 6 |
| ARM | What is ARM's seasonally adjusted unemployment rate? | no | YES | 6 |
| TUR | What is TUR's seasonally adjusted unemployment rate? | no | YES | 6 |
| UZB | What is UZB's seasonally adjusted unemployment rate? | no | YES | 6 |
| MDA | What is MDA's seasonally adjusted unemployment rate? | no | YES | 6 |
| BLR | What is BLR's seasonally adjusted unemployment rate? | no | YES | 6 |
| UKR | What is UKR's seasonally adjusted unemployment rate? | no | YES | 6 |
| RUS | What is RUS's seasonally adjusted unemployment rate? | no | YES | 6 |

</details>

## Reading the results

- **Category 1 (parity):** both modes get it right, at 2 tool calls for
  research mode vs. one deterministic pass for fast mode. This is the
  category `agent/modes.py::select_mode()`'s auto heuristic is supposed to
  route to fast mode — research mode isn't wrong here, just unnecessary
  overhead for a question with no ambiguity to resolve.
- **Categories 2 and 5 (ambiguity):** fast mode isn't merely imprecise, it's
  wrong in a way that would misinform every one of 20 questions — a "real
  GDP growth" answer built from nominal-price data, a "seasonally adjusted"
  figure that isn't. This is precisely the failure mode task section 1's
  architecture diagram exists to fix: a deterministic Top-1 selector has no
  way to read `price_basis`/`seasonally_adjusted` against what the question
  actually asked, because nothing upstream of it extracted that constraint
  in the first place. Research mode's `inspect_series` + `reject_candidate`
  sequence catches it every time in this fixture, with the rejection reason
  recorded in `InvestigationState.candidates_rejected` for audit.
- **Category 3 (multi-source):** not an accuracy gap — an *structural*
  capability gap. `QuestionInterpretation`/`QueryPlan` have no representation
  for "the same concept from two sources" at all; `core/selection.py`
  resolves one concept to exactly one indicator by design (its own
  docstring: "never silently mix incompatible series"). Fast mode cannot
  answer this question correctly no matter how good its NLU is. Research
  mode's `compare_series` tool exists specifically to fill this gap.
- **Category 4 (independent share):** the starkest result — fast mode
  produces **no table at all**, because nothing in its pipeline extracts a
  `share(numerator, denominator)` transformation from freeform text (a real
  `AnthropicPlanner` might do somewhat better than the scripted naive
  planner here, but the ratio still has to be found as two independently
  identified concepts before any transformation can run — see
  [Live follow-up](#live-follow-up)). Research mode retrieves both series
  independently and computes the ratio via the same deterministic
  `core/compose.py::with_share_pair()` fast mode's own transformation
  schema would use — the number itself is never LLM-supplied either way.
- **Tool-call cost:** research mode's real cost is visible in the last
  column — 2 calls for a trivial lookup, 6-7 for anything requiring
  candidate inspection or a second source/series. This is the tradeoff
  `agent/modes.py::AgentLimits` exists to bound, and exactly why fast mode
  should stay the default for questions that don't need it.

## Live follow-up

Running this against a real `ANTHROPIC_API_KEY` would additionally need to
check, none of which this offline run can:

1. Does a real `AnthropicPlanner` (not the deliberately-naive scripted
   interpretation used here) already do better than expected on categories
   2–5 by inferring a smarter concept string, or does the structural
   ambiguity/multi-source/multi-series gap persist even with good NLU? The
   category 3/4 gaps are architectural (no representation in
   `QueryPlan`/`select_indicators()` at all) and should persist regardless
   of NLU quality; categories 2/5 might narrow somewhat with a better
   concept string, but nothing in fast mode's selection scoring
   (`core/selection.py::score_candidate`) reads `price_basis`/
   `seasonally_adjusted` even if the concept text hints at them, so the gap
   likely persists in some form.
2. Does a real `AnthropicAgent` actually choose the same tool sequence a
   human investigator would (this run only proves the *pipeline* works
   correctly *given* a competent sequence, not that the model reliably
   produces one) — this is what `debug=true` on `/ask` (Phase 8) is for:
   inspecting the real tool-call history against real questions.
3. Real latency and token cost per category, to calibrate
   `agent/modes.py::select_mode()`'s auto heuristic and `AgentLimits`'
   defaults against actual usage, not the tool-call-count proxy this run
   provides.
4. Real answer-writer/verifier prose quality (Phases 6–7) — this benchmark
   only exercises the deterministic table/validation path, not
   `write_and_verify_answer()`.

## Recommendation

**Keep fast mode.** Category 1 alone is the reason: it is the majority of
realistic simple queries, and fast mode answers them just as correctly as
research mode at roughly a third of the tool-call cost (and zero LLM calls
for interpretation, if `RuleBasedPlanner` is enough for the question) — task
section 20's explicit instruction not to remove it until research mode is
*proven* better holds up the other way too, since fast mode is proven
better on cost for the category where they tie on correctness.

Route by `agent/modes.py::select_mode()`'s existing auto heuristic (already
implemented, Phase 5): fast mode for direct lookups, research mode for
comparison/explanation/share-style questions, always overridable per
request (`/ask`'s `mode` field, Phase 8). This benchmark is evidence *for*
that design, not against fast mode's continued existence.
