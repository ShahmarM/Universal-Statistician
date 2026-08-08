"""The Provider interface: one implementation per official data source.

Any interface (MCP server, CLI, future API) talks to sources only through this
contract, never through a source's raw client library. That's what lets the
same query engine grow from one source to a dozen without the interfaces
having to know anything changed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from universal_statistician.core.models import IndicatorMeta, SeriesResult


class Provider(ABC):
    source_id: str
    source_name: str

    @abstractmethod
    def get_series(
        self,
        indicator_id: str,
        ref_area: str,
        *,
        start_period: str | None = None,
        end_period: str | None = None,
    ) -> SeriesResult:
        """Fetch one indicator's time series for one area, normalized and attributed."""

    @abstractmethod
    def search(self, query: str, limit: int = 20) -> list[IndicatorMeta]:
        """Search this source's indicator catalog."""

    @abstractmethod
    def describe(self) -> dict:
        """Machine-readable description of this source (id, name, url)."""
