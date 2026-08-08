from __future__ import annotations

from datetime import datetime, timezone

from universal_statistician.core.models import Attribution, Observation, SeriesResult


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
