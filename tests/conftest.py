"""Shared test fixtures.

`sdmx_dataset` builds a real sdmx.model.DataSet in memory (no network call) so
provider parsing logic is exercised against actual library objects and the
real `sdmx.to_pandas` conversion, rather than a hand-guessed shape.
"""

from __future__ import annotations

import os

# Must run before cli.py/api.py/mcp_server.py are ever imported by any test
# module — each builds a default_engine() at import time (module-level
# `_engine = default_engine()`), and since Phase B that opens a real,
# persistent SQLite file under the developer's home directory by default
# (core/engine.py::DEFAULT_CATALOG_DB_PATH). conftest.py is collected before
# any test module in this directory, so setting this here — not inside a
# fixture — guarantees the whole test suite stays on the same non-persistent
# in-memory catalog every other test in this project already assumes,
# without ever touching a real file on the machine running the tests.
os.environ.setdefault("USTAT_CATALOG_DB_PATH", ":memory:")

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
        # Dimension order verified live (Phase H, `pytest -m network`
        # against World Bank/IMF/Eurostat): sdmx.to_pandas() always puts
        # TIME_PERIOD *first* in the resulting index, regardless of how
        # many other dimensions/attributes follow it - never last. An
        # earlier version of this fixture declared TIME_PERIOD last, which
        # is why SDMXProvider._to_series_result()'s old `index_tuple[-1]`
        # bug (grabbing FREQ/an attribute instead of the real period) went
        # undetected offline: the fixture and the bug were wrong the same
        # way. Declaring it first here, matching live reality, is what
        # makes this fixture an actual regression guard again.
        dsd.dimensions.append(TimeDimension(id="TIME_PERIOD", order=1))
        dsd.dimensions.append(Dimension(id="REF_AREA", order=2))
        dsd.dimensions.append(Dimension(id="SERIES", order=3))
        dsd.measures.append(PrimaryMeasure(id="OBS_VALUE"))

        dataset = DataSet(structured_by=dsd)
        for period, value in observations.items():
            key = dsd.make_key(
                Key, {"REF_AREA": ref_area, "SERIES": indicator, "TIME_PERIOD": period}
            )
            dataset.add_obs([Observation(dimension=key, value=value)])
        return dataset

    return _build
