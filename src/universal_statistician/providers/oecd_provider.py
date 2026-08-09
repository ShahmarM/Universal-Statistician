"""OECD provider: SDMXProvider's data retrieval plus catalog discovery via
the National Accounts (NAMAIN10) dataflow's own Data Structure Definition.

Phase I history: OECD was deliberately left unregistered through Phases 1-13
and the live-verification round — no verified working example existed in
either OECD's own documentation or sdmx1's test suite, and the only known
path (stats.oecd.org/SDMX-JSON, the legacy endpoint) needed an unsafe legacy
TLS downgrade this project refused to ship. Revisited here with real,
unrestricted network access:

- sdmx1's own `sdmx.source.sources["OECD"]` now points at
  `https://sdmx.oecd.org/public/rest` — OECD's *current* official SDMX
  endpoint, not the deprecated stats.oecd.org one. No TLS workaround of any
  kind was needed for any request below.
- `sdmx.Client("OECD").dataflow()` lists 1,544 real dataflows live.
  `OECD.SDD.NAD:DSD_NAMAIN10@DF_TABLE1_EXPENDITURE` ("Annual GDP and
  components - expenditure approach") was picked as the one dataflow to
  integrate this round — narrowly scoped, the same way Eurostat/IMF were
  each scoped to one verified dataflow rather than attempting all ~1,500 at
  once.
- The DSD (`DSD_NAMAIN10`) has 12 non-time dimensions (FREQ, REF_AREA,
  SECTOR, COUNTERPART_SECTOR, TRANSACTION, INSTR_ASSET, ACTIVITY,
  EXPENDITURE, UNIT_MEASURE, PRICE_BASE, TRANSFORMATION, TABLE_IDENTIFIER) —
  far more than World Bank's 3 or Eurostat's 4. Building a *correct* key
  required querying live with every dimension wildcarded for one country
  (`A.USA...........`) to see which combination of fixed values actually
  returns data, not guessing from the codelists alone: several plausible-
  looking combinations (e.g. UNIT_MEASURE="USD", ACTIVITY="_T") returned
  404 "NoResultsFound" even though those codes exist in their respective
  codelists — the same "advertised but not populated" gap this project
  already documented for Eurostat/Census in Phase H. The working key,
  `A.USA.S1.S1.B1GQ._Z._Z._Z.USD_EXC.V.N.T0102`, was cross-checked against
  World Bank's own GDP figure for the same country/year (2022:
  26,054,614 million, exact match) and against a direct, independent curl
  to OECD's REST endpoint (not just sdmx1) before being pinned in
  registry.py.
- Discovery uses the exact same pattern as IMFProvider (a direct
  `datastructure` request resolves inline, no external-reference follow-up
  needed — verified live, unlike Eurostat's NAMA_10_GDP) — kept as a
  separate small class rather than reusing IMFProvider itself, matching
  this project's one-file-per-verified-source convention.

Enumerating discovery's ~300 TRANSACTION codes as catalog entries carries
the same honestly-documented limitation already established for Eurostat/
Census: the DSD's codelist advertises real national-accounts concepts (GDP,
final consumption expenditure, gross fixed capital formation, exports,
imports, compensation of employees, ...), but not every code is guaranteed
to have real data for every country under this dataflow's fixed
SECTOR/EXPENDITURE/UNIT_MEASURE/PRICE_BASE/TRANSFORMATION/TABLE_IDENTIFIER
combination — a per-(indicator, country) retrieval concern, not a discovery
bug.
"""

from __future__ import annotations

from universal_statistician.core.catalog import IndicatorEntry
from universal_statistician.providers.sdmx_discovery import SDMXDiscoveryError, entries_from_dsd
from universal_statistician.providers.sdmx_provider import SDMXProvider


class OECDProvider(SDMXProvider):
    def discover_catalog_entries(self) -> list[IndicatorEntry]:
        if not self.config.structure_id:
            raise SDMXDiscoveryError(
                f"{self.config.dataflow_id!r} has no structure_id configured for discovery."
            )

        message = self._client.get("datastructure", resource_id=self.config.structure_id)
        dsd = message.structure[self.config.structure_id]

        return entries_from_dsd(dsd, self.config, source_organization=self.source_name)
