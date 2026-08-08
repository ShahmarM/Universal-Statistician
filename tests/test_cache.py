from __future__ import annotations

from universal_statistician.core.cache import Cache


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_get_missing_key_returns_none():
    cache = Cache(time_func=FakeClock())
    assert cache.get("missing") is None


def test_set_then_get_round_trips_value():
    cache = Cache(time_func=FakeClock())
    cache.set("key", {"a": 1}, ttl_seconds=60)
    assert cache.get("key") == {"a": 1}


def test_entry_expires_after_ttl():
    clock = FakeClock()
    cache = Cache(time_func=clock)
    cache.set("key", {"a": 1}, ttl_seconds=10)

    clock.now += 5
    assert cache.get("key") == {"a": 1}

    clock.now += 6  # total 11s elapsed, past the 10s TTL
    assert cache.get("key") is None


def test_set_overwrites_existing_key():
    cache = Cache(time_func=FakeClock())
    cache.set("key", {"a": 1}, ttl_seconds=60)
    cache.set("key", {"a": 2}, ttl_seconds=60)
    assert cache.get("key") == {"a": 2}


def test_make_key_is_stable_and_position_sensitive():
    assert Cache.make_key("WB_WDI", "SP_POP_TOTL", "AFG") == Cache.make_key(
        "WB_WDI", "SP_POP_TOTL", "AFG"
    )
    assert Cache.make_key("WB_WDI", "SP_POP_TOTL", "AFG") != Cache.make_key(
        "WB_WDI", "AFG", "SP_POP_TOTL"
    )


def test_make_key_is_consistent_for_none_parts():
    # start_period/end_period are None for an unbounded query; None must
    # always serialize to the same thing so repeat unbounded queries hit
    # the same cache entry.
    assert Cache.make_key("A", None) == Cache.make_key("A", None)
