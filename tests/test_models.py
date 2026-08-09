from __future__ import annotations

from datetime import datetime, timezone

from universal_statistician.core.models import (
    Attribution,
    Observation,
    SeriesResult,
    StatisticalSemantics,
)


def test_series_result_round_trips_through_dict():
    result = SeriesResult(
        indicator_id="SP_POP_TOTL",
        ref_area="AFG",
        frequency="A",
        observations=(
            Observation(period="2019", value=100.0),
            Observation(period="2020", value=None),
        ),
        attribution=Attribution(
            source_id="WB_WDI",
            source_name="World Bank — World Development Indicators",
            dataset_id="WDI",
            retrieved_at=datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc),
            source_url="https://example.org",
        ),
    )

    restored = SeriesResult.from_dict(result.as_dict())

    assert restored == result


def test_series_result_round_trips_semantics_through_dict():
    result = SeriesResult(
        indicator_id="B1GQ",
        ref_area="LU",
        frequency="A",
        observations=(Observation(period="2020", value=1.5),),
        attribution=Attribution(
            source_id="ESTAT",
            source_name="Eurostat",
            dataset_id="NAMA_10_GDP",
            retrieved_at=datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc),
        ),
        unit="EUR million, current prices",
        semantics=StatisticalSemantics(
            price_basis="nominal", currency="EUR", currency_scale="millions"
        ),
    )

    restored = SeriesResult.from_dict(result.as_dict())

    assert restored == result
    assert restored.semantics.price_basis == "nominal"


def test_statistical_semantics_defaults_to_all_unknown():
    semantics = StatisticalSemantics()

    assert semantics.price_basis is None
    assert semantics.per_capita is None
    assert semantics.seasonally_adjusted is None
    restored = StatisticalSemantics.from_dict(semantics.as_dict())
    assert restored == semantics
