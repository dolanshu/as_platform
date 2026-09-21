# Copyright 2026 Dolan Shu <dolan.d.shu@gmail.com>.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for the state-store seam and both implementations.

P10 shipped InMemoryStateStore as the only implementation (ADR-0009 decision 5). P11 adds
RedisStateStore as the proof that the seam is pluggable (ADR-0010 decision 3, REQ-F-035).
Redis tests require a running Redis server on localhost:6379 and are skipped when unavailable.
"""

from __future__ import annotations

import threading

import pytest

from as_platform.state_store import InMemoryStateStore


def test_read_of_an_absent_key_returns_none() -> None:
    """An empty store answers ``None``."""
    assert InMemoryStateStore().read("missing") is None


def test_write_then_read_returns_the_stored_value() -> None:
    """A written record is returned unchanged; the store never interprets it."""
    store = InMemoryStateStore()
    value = {"count": 2}
    store.write("caller:a", value)
    assert store.read("caller:a") is value


def test_read_marks_the_key_most_recently_used() -> None:
    """A read re-orders the key, so the untouched key is evicted first."""
    store = InMemoryStateStore()
    store.write("a", 1)
    store.write("b", 2)
    store.read("a")
    store.trim("", limit=1)
    assert store.read("a") == 1
    assert store.read("b") is None


def test_trim_drops_the_least_recently_used_keys_of_the_family() -> None:
    """The bound keeps the newest keys and drops the oldest of that family."""
    store = InMemoryStateStore()
    for key in ("w:a", "w:b", "w:c"):
        store.write(key, key)
    store.trim("w:", limit=2)
    assert store.read("w:a") is None
    assert store.read("w:b") == "w:b"
    assert store.read("w:c") == "w:c"


def test_trim_counts_only_the_key_family_its_prefix_names() -> None:
    """A bound on one family leaves the other family's keys untouched."""
    store = InMemoryStateStore()
    for key in ("w:a", "w:b", "w:c", "r:a", "r:b", "r:c"):
        store.write(key, key)
    store.trim("w:", limit=1)
    assert store.read("w:c") == "w:c"
    assert store.read("w:b") is None
    assert store.read("r:a") == "r:a"
    assert store.read("r:b") == "r:b"
    assert store.read("r:c") == "r:c"


def test_a_trim_within_the_limit_drops_nothing() -> None:
    """A family already under its bound is left alone."""
    store = InMemoryStateStore()
    store.write("w:a", 1)
    store.trim("w:", limit=5)
    assert store.read("w:a") == 1


def test_in_memory_store_start_and_stop_are_no_ops() -> None:
    """InMemoryStateStore has no background resources to manage."""
    store = InMemoryStateStore()
    store.start()
    store.stop()


# ---- RedisStateStore tests ----


def _redis_available() -> bool:
    """Return True if Redis client package is importable and a server is reachable."""
    try:
        import redis as _redis
    except ImportError:
        return False
    try:
        r = _redis.from_url("redis://localhost:6379/15")
        r.ping()
        return True
    except Exception:
        return False


requires_redis = pytest.mark.skipif(not _redis_available(), reason="Redis not available")


@requires_redis
class TestRedisStateStore:
    """RedisStateStore tests — skipped when Redis is down or not installed."""

    @pytest.fixture(autouse=True)
    def _cleanup(self) -> None:
        """Flush test db before and after each test."""
        import redis as _redis

        r = _redis.from_url("redis://localhost:6379/15")
        r.flushdb()
        self.store = __import__(
            "as_platform.state_store", fromlist=["RedisStateStore"]
        ).RedisStateStore(redis_url="redis://localhost:6379/15")
        self.store.start()
        try:
            yield
        finally:
            self.store.stop()
            r.flushdb()

    def test_read_of_missing_key_returns_none(self) -> None:
        assert self.store.read("missing") is None

    def test_write_then_read_round_trips_json(self) -> None:
        self.store.write("caller:bob", {"windows": [1, 2, 3], "score": 0.5})
        result = self.store.read("caller:bob")
        assert result == {"windows": [1, 2, 3], "score": 0.5}

    def test_overwrite_replaces_the_value(self) -> None:
        self.store.write("k", 1)
        self.store.write("k", 2)
        assert self.store.read("k") == 2

    def test_trim_best_effort_evicts_excess(self) -> None:
        for i in range(6):
            self.store.write(f"w:{i}", i)
        self.store.trim("w:", limit=3)
        remaining = [self.store.read(f"w:{i}") for i in range(6)]
        non_null = [v for v in remaining if v is not None]
        assert len(non_null) <= 3

    def test_store_works_from_multiple_threads(self) -> None:
        """Multiple callers on different threads don't corrupt data."""
        errors: list[Exception] = []

        def writer() -> None:
            try:
                for i in range(20):
                    self.store.write(f"shared:{threading.get_ident()}:{i}", i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors
