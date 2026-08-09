from __future__ import annotations

import pytest

from universal_statistician.core.catalog import Catalog
from universal_statistician.core.ingestion import ingest_source
from universal_statistician.providers.base import MetadataDiscoverable
from universal_statistician.providers.census_provider import CensusMissingApiKeyError, CensusProvider
from universal_statistician.providers.census_registry import SOURCES


@pytest.fixture
def provider():
    return CensusProvider(SOURCES["US_CENSUS_ACS1"])


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeSession:
    """Records requested URLs/params and serves canned per-year responses —
    no real HTTP call. Reproduces the real API's documented shape (see
    census_provider.py's docstring): a 2D array, header row + one data row."""

    def __init__(self, by_year: dict[str, dict], *, missing_key_years: set[str] = frozenset()):
        self._by_year = by_year
        self._missing_key_years = missing_key_years
        self.requests: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None):
        self.requests.append((url, params or {}))
        parts = url.split("/")
        year = parts[parts.index("data") + 1]  # .../data/{year}/acs/acs1
        if year in self._missing_key_years:
            # Real behavior verified live (Phase H): an unauthenticated
            # request 302-redirects to an HTML page; requests follows it,
            # so this looks like an ordinary 200 with this one header set,
            # not a 4xx.
            return _FakeResponse(status_code=200, headers={"X-DataWebAPI-KeyError": "1"})
        if year not in self._by_year:
            return _FakeResponse(status_code=404)
        return _FakeResponse(payload=self._by_year[year])


def test_get_series_requires_both_periods(provider):
    with pytest.raises(ValueError, match="start_period and end_period"):
        provider.get_series("B01003_001E", "01")


def test_get_series_makes_one_request_per_year(monkeypatch, provider):
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    fake = _FakeSession(
        {
            "2019": [["NAME", "B01003_001E", "state"], ["Alabama", "4876250", "01"]],
            "2020": [["NAME", "B01003_001E", "state"], ["Alabama", "4893186", "01"]],
        }
    )
    monkeypatch.setattr(provider, "_session", fake)

    result = provider.get_series("B01003_001E", "01", start_period="2019", end_period="2020")

    assert [o.period for o in result.observations] == ["2019", "2020"]
    assert [o.value for o in result.observations] == [4876250.0, 4893186.0]
    assert len(fake.requests) == 2
    assert fake.requests[0][1] == {"get": "NAME,B01003_001E", "for": "state:01"}


def test_get_series_includes_the_api_key_when_configured(monkeypatch, provider):
    monkeypatch.setenv("CENSUS_API_KEY", "test-key-123")
    fake = _FakeSession({"2020": [["NAME", "B01003_001E", "state"], ["Alabama", "4893186", "01"]]})
    monkeypatch.setattr(provider, "_session", fake)

    provider.get_series("B01003_001E", "01", start_period="2020", end_period="2020")

    assert fake.requests[0][1]["key"] == "test-key-123"


def test_get_series_raises_a_clear_error_when_the_api_key_is_missing(monkeypatch, provider):
    # Real behavior verified live (Phase H): an unauthenticated request
    # 302-redirects to an HTML "missing key" page, not a clean 4xx - without
    # this check, response.json() would raise an opaque JSONDecodeError.
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    fake = _FakeSession({}, missing_key_years={"2020"})
    monkeypatch.setattr(provider, "_session", fake)

    with pytest.raises(CensusMissingApiKeyError, match="CENSUS_API_KEY"):
        provider.get_series("B01003_001E", "01", start_period="2020", end_period="2020")


def test_get_series_skips_years_with_a_404_instead_of_failing(monkeypatch, provider):
    fake = _FakeSession(
        {"2020": [["NAME", "B01003_001E", "state"], ["Alabama", "4893186", "01"]]}
    )
    monkeypatch.setattr(provider, "_session", fake)

    result = provider.get_series("B01003_001E", "01", start_period="2019", end_period="2020")

    assert [o.period for o in result.observations] == ["2020"]


def test_get_series_attributes_to_the_configured_dataset(monkeypatch, provider):
    fake = _FakeSession(
        {"2020": [["NAME", "B01003_001E", "state"], ["Alabama", "4893186", "01"]]}
    )
    monkeypatch.setattr(provider, "_session", fake)

    result = provider.get_series("B01003_001E", "01", start_period="2020", end_period="2020")

    assert result.attribution.source_id == "US_CENSUS_ACS1"
    assert result.attribution.dataset_id == "acs/acs1"
    assert result.frequency == "A"


def test_describe_exposes_dataset_path(provider):
    description = provider.describe()
    assert description["source_id"] == "US_CENSUS_ACS1"
    assert description["dataset_path"] == "acs/acs1"


def test_census_provider_is_metadata_discoverable(provider):
    assert isinstance(provider, MetadataDiscoverable)


_VARIABLES_PAYLOAD = {
    "variables": {
        "B01003_001E": {
            "label": "Estimate!!Total",
            "concept": "TOTAL POPULATION",
            "predicateType": "int",
            "group": "B01003",
        },
        "NAME": {"label": "Geographic Area Name", "predicateType": "string", "group": "N/A"},
        "GEO_ID": {"label": "Geography", "predicateType": "string", "group": "N/A"},
    }
}


def test_discover_catalog_entries_excludes_geography_variables(monkeypatch, provider):
    fake = _FakeSession({})
    fake.get = lambda url, params=None, timeout=None: _FakeResponse(payload=_VARIABLES_PAYLOAD)
    monkeypatch.setattr(provider, "_session", fake)

    entries = provider.discover_catalog_entries()

    assert {e.indicator_id for e in entries} == {"B01003_001E"}
    entry = entries[0]
    assert entry.names == {"en": "Estimate!!Total"}
    assert entry.description == "TOTAL POPULATION"
    assert entry.source_id == "US_CENSUS_ACS1"
    assert entry.dataset_id == "acs/acs1"
    assert entry.frequency == "A"


def test_discover_catalog_entries_ingests_into_the_catalog(monkeypatch, provider):
    fake = _FakeSession({})
    fake.get = lambda url, params=None, timeout=None: _FakeResponse(payload=_VARIABLES_PAYLOAD)
    monkeypatch.setattr(provider, "_session", fake)

    catalog = Catalog()
    report = ingest_source("US_CENSUS_ACS1", provider, catalog)

    assert report.ok
    assert report.added == 1
    assert catalog.get("US_CENSUS_ACS1", "B01003_001E") is not None


@pytest.mark.network
def test_live_get_series_smoke():
    """Real call against the live Census API — see census_provider.py's
    docstring: this is the one thing the offline tests above can't confirm
    (the documented shape, not whether the live API still matches it).

    Verified live (Phase H): the query endpoint requires CENSUS_API_KEY
    (an unauthenticated request redirects to an HTML "missing key" page,
    not a 4xx) - skip cleanly rather than fail when no key is configured,
    the same principle as ANTHROPIC_API_KEY-gated live tests elsewhere."""
    import os

    if not os.environ.get("CENSUS_API_KEY"):
        pytest.skip("CENSUS_API_KEY not set - free key at https://api.census.gov/data/key_signup.html")

    result = CensusProvider(SOURCES["US_CENSUS_ACS1"]).get_series(
        "B01003_001E", "01", start_period="2021", end_period="2021"
    )
    assert result.observations


@pytest.mark.network
def test_live_discover_catalog_entries_smoke():
    entries = CensusProvider(SOURCES["US_CENSUS_ACS1"]).discover_catalog_entries()
    assert any(e.indicator_id == "B01003_001E" for e in entries)
