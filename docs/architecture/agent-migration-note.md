# Migration note: single-pass `/ask` → iterative `StatisticalAgent`

Written before any code changes, per the task's "first implementation
steps." Traces the current `/ask` path function-by-function and states
what happens to each piece.

## Current `/ask` path, traced

`api.py::ask()` → `tools.ask()` → `core/ask.py::answer_question()`, which
calls, in this exact order:

1. **Planning** — `planner.interpret(question)` (`planning/anthropic_planner.py::AnthropicPlanner`
   or `planning/rule_based_planner.py::RuleBasedPlanner`). One forced tool
   call, one shot, no ability to revise after seeing results.
2. **Candidate resolution** — `core/query_plan.py::build_query_plan()`.
   Calls `QueryEngine.search_indicator()` (→ `Catalog.search()`) once per
   concept, keeps the top `candidates_per_concept` (default 5) as
   `CandidateIndicator` objects on `QueryPlan.candidate_indicators`.
3. **Selection** — `core/selection.py::select_indicators()`. Deterministic
   `score_candidate()` scoring (name match, geographic coverage,
   frequency, search rank — Phase H) picks exactly one candidate per
   concept and writes `QueryPlan.selected_indicators`. **This is the
   blind Top-1 step the task calls out** — the LLM never sees the
   alternatives that were rejected or why.
4. **Retrieval** — `core/ask.py::_fetch_table()`. Loops
   `selected_indicators × geographies`, calls `QueryEngine.get_series()`
   for each, assembles a `ComparisonTable` (`core/compose.py`). Every
   provider call the pipeline will ever make is decided before this step
   starts — no result can trigger another retrieval.
5. **Transformations** — `core/ask.py::_apply_transformations()`. Dispatches
   `QueryPlan.transformations` (structured `TransformationSpec`s, Phase D)
   onto `core/compose.py`'s `with_*()` functions by name.
6. **Validation** — `core/validation.py::validate_table()`. Structured
   PASS/WARNING/FAIL findings.
7. **Answer text** — `core/ask.py::_build_answer_text()`. A Python string
   template over the table's latest values — never an LLM.
8. **Chart** — `core/ask.py::_build_chart_spec()`.
9. **Sources/provenance** — `core/ask.py::_unique_sources()` /
   `_provenance_for_latest_period()` → `core/provenance.py::resolve_provenance()`.

## What remains exactly as-is (deterministic core, per the task's hard boundary)

- `providers/*` — every provider (SDMX/PX-Web/Census), retrieval, caching
  (`core/cache.py`), catalog storage (`core/catalog.py`).
- `core/compose.py` — every `with_*()` calculation function. The new
  `calculate` tool (Phase 1/4) calls these directly; it does not
  reimplement any formula.
- `core/validation.py` — `validate_table()`/`validate_series()` unchanged;
  wrapped by a `validate` tool, not rewritten.
- `core/provenance.py` — `resolve_provenance()` unchanged; wrapped by an
  `inspect_provenance` tool.
- `core/geography.py`, `core/models.py`, `core/engine.py`'s
  `get_series()`/`search_indicator()`/provider dispatch.
- `api.py`'s non-`/ask` endpoints, `tools.py`, `mcp_server.py`, `cli.py` —
  untouched; they call the same `QueryEngine`/`tools.py` functions they
  always did.

## What gets wrapped, not rewritten

- `Catalog.search()` → `search_series` tool. The ranking (Phase C's
  composite scoring) stays and still orders results — it just stops being
  the thing that makes the *final* choice (task section: "downgrade from
  'choose the indicator' to 'rank candidate indicators for the LLM'").
- `QueryEngine.get_series()` → `retrieve_series` tool. Same cache, same
  providers, same `Attribution`.
- `core/compose.py`'s `with_*()` functions → `calculate` tool, dispatched
  through a strict operation enum (Phase 4's `agent/expressions.py`),
  never a bare string or LLM-supplied number.
- `validate_table()` → `validate` tool.
- `resolve_provenance()` → `inspect_provenance` tool.
- `core/query_plan.py::TransformationSpec`/`QuestionInterpretation` — the
  *shape* (structured, concept-referencing, never a raw code/number) is
  the model the new `calculate` tool's request schema follows; the
  concept-resolution machinery itself (`build_query_plan()`) stays as the
  fast-mode/legacy path's mechanism.

## What gets replaced, in the new primary path only

- **Blind Top-1 selection** (`select_indicators()` as an automatic,
  unreviewable step) — replaced by the LLM inspecting `search_series`
  results (with rank order as a *hint*, not a verdict) via `inspect_series`
  and choosing explicitly, with rejected candidates and reasons recorded
  in `InvestigationState`.
- **One-shot, fully-pre-planned retrieval** (`build_query_plan()` deciding
  every provider call before the first one happens) — replaced by the
  iterative loop: retrieve, inspect, decide whether another retrieval is
  needed.
- **Python-template-only answer text** (`_build_answer_text()`) — replaced
  by a separate answer-writing LLM pass (Phase 6) constrained to the
  validated evidence set, with a numeric-consistency guard. The old
  template becomes fast mode's answer text (still deterministic, still
  useful for the low-latency path).
- **Bare/ambiguous transformation strings** — already structured since
  Phase D (`TransformationSpec`); the agent's `calculate` tool goes one
  step further by referencing concrete retrieved/derived *result IDs*
  instead of natural-language concepts, since by the time the agent calls
  `calculate` it has already retrieved the specific series it means.
- **Presenting a numerical answer after validation FAIL** — the answer
  writer refuses to state derived numbers found FAIL; the orchestrator
  routes a FAIL to either another investigation iteration or an explicit
  "cannot be answered" response, never silently through.

## What becomes legacy/fallback (kept, not deleted)

- `core/ask.py::answer_question()` stays as the **fast-mode** engine
  (task section 7) — unchanged internally, still used for simple
  lookups where the iterative loop's extra latency isn't worth it, and
  still the mechanism manual/API callers and existing tests exercise
  directly. `select_indicators()` likewise stays as fast mode's
  selection step and the function tests/manual API use call directly.
- `planning/rule_based_planner.py::RuleBasedPlanner` — still the no-LLM
  fallback for `/plan` and fast mode when no `ANTHROPIC_API_KEY` is
  configured (unchanged: "the platform remains operational... without an
  LLM," section 22 of the earlier spec).
- `planning/anthropic_planner.py::AnthropicPlanner` — still used by fast
  mode's single-shot interpretation step; the new `agent/` package adds a
  *different* LLM role (iterative investigator) rather than replacing this
  one, per section 22's `LLMAgent`/`LLMAnswerWriter`/`LLMVerifier`
  split (this planner is closest in spirit to a "propose one plan" tool,
  not a multi-turn agent).

## New code (this migration)

All under `src/universal_statistician/agent/` (a new package, not a single
`core/agent.py`, since the task's own phase breakdown — tools, state,
loop, expressions, modes, answer writer, verifier — is naturally several
focused modules rather than one file):

- `agent/tools.py` — the trusted tool layer (Phase 1).
- `agent/state.py` — `InvestigationState` (Phase 2).
- `agent/loop.py` — `StatisticalAgent`, the iterative tool-call loop
  (Phase 2), stopping rules/limits (task section 6).
- `agent/expressions.py` — the structured calculation request schema +
  executor (Phase 4).
- `agent/modes.py` — fast/research/auto mode selection (Phase 5).
- `agent/answer_writer.py` — the answer-writing pass + numeric-consistency
  guard (Phase 6).
- `agent/verifier.py` — the verification pass (Phase 7).
- `agent/llm.py` — vendor-neutral `LLMAgent`/`LLMAnswerWriter`/
  `LLMVerifier` abstractions, one Anthropic-backed implementation each
  (task section 22: "one working provider plus clean abstraction is
  enough").

## Order of implementation

Matches the task's 9 phases exactly; each is a separate commit against
`claude/universal-stats-assistant-fhr8bl`, offline test suite green before
every commit.
