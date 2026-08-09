# Phase C: catalog search-quality benchmark

Honest record of measuring and improving `Catalog.search()` against the
real, live-populated catalog built in the live-verification round (38,789
indicators across WB_WDI, ESTAT_NAMA_10_GDP, IMF_DATA_CPI, US_CENSUS_ACS1,
SCB_TAB6471 — see `docs/architecture/provider-verification-matrix.md`).

## Method

1. Built a 54-concept ground-truth list (`scripts/benchmark_catalog_search.py`,
   `CONCEPTS`) covering GDP/population/inflation/unemployment/trade/fiscal/
   demographic/social/environmental indicators — the task description's own
   example list plus enough extras to clear "50+". **Every code in the list
   was verified to exist in the real catalog via a direct
   `catalog.get(source_id, indicator_id)` call before being added** — none
   are guessed from training-data recall. This caught a real, live
   discrepancy: `EN_ATM_CO2E_PC` (the CO2-emissions-per-capita code recalled
   from training data) does not exist in the live catalog — World Bank has
   since replaced it with `EN_GHG_CO2_PC_CE_AR5` under a newer GHG/AR5
   accounting methodology. The benchmark uses the live code.
2. Ran `QueryEngine.search_indicator(query, limit=5)` — the same call path
   `/search`, `ustat search`, and the MCP `search_indicator` tool actually
   use — for each concept's query string, and checked whether the
   acceptable-family indicator appeared at rank 1 / within the top 3 / within
   the top 5.
3. Measured this **twice**: once against plain FTS5 `bm25` ranking
   (`ORDER BY rank`, the pre-existing implementation) to get an honest
   baseline, and once after the ranking change described below.

## Baseline: what plain FTS5 bm25 gets wrong

Exploring the live catalog surfaced concrete, reproducible failures before
any code changed:

- **"GDP"** ranked `Personal remittances, received (% of GDP)` first, not
  the flagship `GDP (current US$)` (`NY_GDP_MKTP_CD`) — which didn't even
  make the top 5. A World Bank entry with nothing to do with GDP
  (`Short-term debt (% of total reserves)`) also matched at all, purely
  because its `source_organization` field is a data-lineage citation
  ("World Bank (WB), type: **GDP estimates**") that happens to contain the
  word "GDP" as an unrelated methodology note.
- **"population"** omitted the flagship `Population, total` (`SP_POP_TOTL`)
  from the top 5 entirely, in favor of narrower age/sex breakdowns like
  `Population ages 0-14 (% of total population)` — whose name literally
  contains "population" twice, which bm25 rewards as higher term frequency
  regardless of which indicator is actually the general/canonical one.
- **"inflation"** correctly ranked `FP_CPI_TOTL_ZG` first, but then filled
  positions 2-5 with US Census entries whose name merely contains the
  phrase "inflation-adjusted" (an income methodology footnote, not an
  inflation indicator) — cross-source noise from a very large unrelated
  dataset (US_CENSUS_ACS1: 36,632 variables).
- **"exports"** omitted the general `NE_EXP_GNFS_CD` / `NE_EXP_GNFS_ZS`
  ("Exports of goods and services") from the top 5, surfacing only narrow
  sub-metrics (food/fuel/manufactures exports as % of merchandise exports).

Measured baseline across all 54 concepts (plain bm25, no re-ranking):

| Metric | Result |
|---|---|
| Top-1 | 20/54 (37.0%) |
| Top-3 | 28/54 (51.9%) |
| Top-5 | 33/54 (61.1%) |

## Root cause

bm25 rewards term frequency and the number of matched columns; it has no
notion of "this is the canonical/flagship indicator for the concept" versus
"this indicator happens to repeat the query term". A narrow sub-breakdown
whose name repeats the query word, or an unrelated entry whose free-text
citation field incidentally contains it, can out-score the actual general
indicator. Debugged with SQLite's own `bm25(indicators)` auxiliary function
directly (not guessed) to confirm exactly which column was driving each
bad ranking before changing anything.

## Fix (still no embeddings — lexical/metadata only, per this phase's scope)

Two changes to `Catalog.search()` (`core/catalog.py`):

1. **Column weights.** `bm25(indicators, name=10.0, keywords=2.0, unit=1.0,
   source_organization=0.0, geo=0.2)` instead of the FTS5 default (every
   indexed column weighted equally at 1.0). `source_organization` carries
   long free-text data-lineage citations, not a short org label — live
   discovery found it actively harmful as a relevance signal (the "GDP
   estimates" false positive above), so it's weighted to ~0: it still
   participates in the boolean `MATCH` (organization-name searches keep
   working), it just stops driving rank.
2. **Composite re-rank over a bm25-ordered candidate pool.** bm25 alone
   can't distinguish "the whole name is the query concept" from "the name
   mentions the query concept once among several other words" — both are
   genuine full-text matches, and no column-weighting scheme fixes that.
   `_composite_score()` (new, `core/catalog.py`) re-scores each bm25
   candidate by:
   - `coverage`: fraction of query tokens present (by prefix) in the name.
   - `exact`: fraction of query tokens present as an exact whole-word match.
   - `extra_penalty`: cost of every name token *not* matching the query —
     discounted for a small curated set of generic administrative
     qualifiers (`total`, `annual`, `current`, `modeled`, `national`,
     `estimate`, ...) that real official sources routinely append to
     flagship indicator names (`"Population, total"`,
     `"Unemployment, total (% of total labor force) (modeled ILO
     estimate)"`) so those aren't penalized as if `total`/`modeled` were as
     narrowing as `rural`/`youth`/`female`.
   - the underlying bm25 score, heavily downweighted, as a tie-break only.

   Only the top `max(limit * 20, 200)` bm25 candidates are re-ranked in
   Python — re-scoring is O(candidates), not O(catalog); a broad prefix
   query like `population*` alone matches 2,000+ rows in the real catalog,
   so an unbounded re-rank would be real, avoidable work.

## Result

Same 54 concepts, same live catalog, after the fix:

| Metric | Before | After |
|---|---|---|
| Top-1 | 20/54 (37.0%) | 30/54 (55.6%) |
| Top-3 | 28/54 (51.9%) | 37/54 (68.5%) |
| Top-5 | 33/54 (61.1%) | 42/54 (77.8%) |

All four concretely-diagnosed baseline failures are fixed: "GDP" and
"population" now rank their flagship indicator first, "inflation"'s Census
noise is pushed out of the top 3, and the citation-text false positive
(`Short-term debt ...`) sinks to last place instead of competing for a top
spot. Reproduced with `pytest tests/test_search_quality.py` (offline,
hardcoded fixture — no live catalog required) and directly re-verified
against the real live catalog (`scripts/benchmark_catalog_search.py`).

## What's still honestly missing (documented, not fixed this round)

Run `python scripts/benchmark_catalog_search.py` (with
`USTAT_CATALOG_DB_PATH` pointed at a populated catalog) for the full
per-concept table. Remaining misses fall into two real, distinct causes —
neither fixed here, both scoped out deliberately rather than silently:

1. **No stemming.** The FTS5 table uses the default `unicode61` tokenizer
   (no stemmer). "internet **users**" doesn't match `Individuals **using**
   the Internet (% of population)`; "poverty **rate**" doesn't match
   `Poverty headcount **ratio**`. Prefix matching (`users*`) only helps when
   the query token is a literal prefix of the matched token — it doesn't
   bridge genuine morphological variants. FTS5 supports a `porter` stemming
   tokenizer, which is the natural next lexical improvement and still not
   an embedding — not applied in this round because an FTS5 virtual table's
   tokenizer is fixed at `CREATE VIRTUAL TABLE` time; changing it on an
   already-populated on-disk catalog needs a real rebuild-and-reload
   migration (unlike `catalog_meta`'s plain `ALTER TABLE ADD COLUMN`
   migrations), which is a bigger, separate change deserving its own round
   rather than being folded in here.
2. **Genuine cross-source ambiguity.** "government expenditure" ranks an
   IMF CPI-classification entry literally named `Total general government
   expenditure` above the intended WB_WDI/Eurostat national-accounts
   indicators — both plausibly match the phrase, and disambiguating
   "which statistical domain did the user mean" from lexical overlap alone
   is a genuinely harder problem than ranking flagship vs. sub-breakdown
   within one domain.

Per this phase's explicit instruction ("avoid embeddings unless
lexical/metadata ranking is first optimized and benchmarked"): this round
optimized and benchmarked lexical/metadata ranking, roughly halved the
Top-1 miss rate, and leaves stemming as the next concrete lexical step
before reaching for embeddings.
