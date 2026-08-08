"""Shared test fixtures.

`sdmx_dataset` builds a real sdmx.model.DataSet in memory (no network call) so
provider parsing logic is exercised against actual library objects and the
real `sdmx.to_pandas` conversion, rather than a hand-guessed shape.
"""

from __future__ import annotations

import pytest
from sdmx.model.v21 import (
    DataSet,
    DataStructureDefinition,
    Dimension,
    Key,
    Observation,
    PrimaryMeasure,
    TimeDimension,
)


@pytest.fixture
def sdmx_dataset():
    def _build(observations: dict[str, float | None], ref_area: str, indicator: str) -> DataSet:
        dsd = DataStructureDefinition(id="TEST_DSD")
        dsd.dimensions.append(Dimension(id="REF_AREA", order=1))
        dsd.dimensions.append(Dimension(id="SERIES", order=2))
        dsd.dimensions.append(TimeDimension(id="TIME_PERIOD", order=3))
        dsd.measures.append(PrimaryMeasure(id="OBS_VALUE"))

        dataset = DataSet(structured_by=dsd)
        for period, value in observations.items():
            key = dsd.make_key(
                Key, {"REF_AREA": ref_area, "SERIES": indicator, "TIME_PERIOD": period}
            )
            dataset.add_obs([Observation(dimension=key, value=value)])
        return dataset

    return _build
