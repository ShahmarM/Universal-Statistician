from __future__ import annotations

import pytest

from universal_statistician.providers.worldbank_discovery import (
    DISCOVERY_URL,
    discover_wb_wdi_entries,
    fetch_pages,
    parse_indicator,
)

# A payload shaped exactly like World Bank's own documented v2 Indicators
# API response (https://datahelpdesk.worldbank.org/knowledgebase/articles/898581):
# a real, published field shape, not a guess — see worldbank_discovery.py's
# module docstring for the honesty caveat (not exercised live in this sandbox).
_RAW_GDP_PER_CAPITA = {
    "id": "NY.GDP.PCAP.CD",
    "name": "GDP per capita (current US$)",
    "unit": "",
    "source": {"id": "2", "value": "World Development Indicators"},
    "sourceNote": "GDP per capita is gross domestic product divided by midyear population.",
    "sourceOrganization": "World Bank national accounts data, and OECD National Accounts data files.",
    "topics": [{"id": "3", "value": "Economy & Growth"}],
}


def test_parse_indicator_builds_entry_from_the_documented_shape():
    entry = parse_indicator(_RAW_GDP_PER_CAPITA)

    assert entry.indicator_id == "NY.GDP.PCAP.CD"
    assert entry.source_id == "WB_WDI"
    assert entry.names == {"en": "GDP per capita (current US$)"}
    assert entry.description == (
        "GDP per capita is gross domestic product divided by midyear population."
    )
    assert entry.dataset_id == "WDI"
    assert entry.unit is None  # WB sends "" for indicators with no fixed unit label
    assert entry.frequency == "A"
    assert entry.source_organization == (
        "World Bank national accounts data, and OECD National Accounts data files."
    )
    assert entry.official_url
    assert entry.keywords == ("Economy & Growth",)


def test_parse_indicator_handles_missing_optional_fields():
    entry = parse_indicator({"id": "SP_POP_TOTL"})

    assert entry.indicator_id == "SP_POP_TOTL"
    assert entry.names == {"en": "SP_POP_TOTL"}  # falls back to the code as a label
    assert entry.description is None
    assert entry.unit is None
    assert entry.source_organization is None
    assert entry.keywords is None


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    """Records requested pages and serves a canned two-page result set,
    without any real HTTP call — the pagination *loop* is what's under
    test, not the live API (that needs the network-marked test below)."""

    def __init__(self, pages: dict[int, dict]):
        self._pages = pages
        self.requested_pages: list[int] = []

    def get(self, url, params, timeout):
        assert url == DISCOVERY_URL
        page = params["page"]
        self.requested_pages.append(page)
        return _FakeResponse(self._pages[page])


def test_fetch_pages_paginates_until_exhausted():
    session = _FakeSession(
        {
            1: [{"page": 1, "pages": 2}, [{"id": "A"}, {"id": "B"}]],
            2: [{"page": 2, "pages": 2}, [{"id": "C"}]],
        }
    )

    results = list(fetch_pages(session, per_page=2))

    assert [r["id"] for r in results] == ["A", "B", "C"]
    assert session.requested_pages == [1, 2]


def test_fetch_pages_stops_after_a_single_page_when_pages_is_one():
    session = _FakeSession({1: [{"page": 1, "pages": 1}, [{"id": "ONLY"}]]})

    results = list(fetch_pages(session))

    assert [r["id"] for r in results] == ["ONLY"]
    assert session.requested_pages == [1]


def test_discover_wb_wdi_entries_parses_every_fetched_page():
    session = _FakeSession({1: [{"page": 1, "pages": 1}, [_RAW_GDP_PER_CAPITA]]})

    entries = discover_wb_wdi_entries(session)

    assert len(entries) == 1
    assert entries[0].indicator_id == "NY.GDP.PCAP.CD"


@pytest.mark.network
def test_live_discovery_returns_a_large_indicator_set():
    """Real call against the live WB v2 API — see this module's docstring:
    this is the one thing offline tests above can't confirm (the documented
    shape, not whether the live endpoint still matches it)."""
    entries = discover_wb_wdi_entries()
    assert len(entries) > 1000  # WDI has on the order of 1,500 indicators
    assert any(e.indicator_id == "SP.POP.TOTL" for e in entries)
