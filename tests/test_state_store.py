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

"""Tests for the state-store seam: reads, writes, LRU eviction and per-family trim.

The anti-fraud keeps two independent structures with two independent bounds in one
store, so ``trim`` carries a ``prefix`` and only counts the key family it names
(ADR-0009 decision 5, D9).
"""

from __future__ import annotations

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
