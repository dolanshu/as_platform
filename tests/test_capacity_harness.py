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

"""Tests for the capacity harness module (P11, REQ-F-037).

The harness is a library component that drives call factories at increasing offered
concurrency levels and records per-level observations. It does not publish benchmark
numbers — only the structured data the caller asked for (D10, REQ-NF-025).
"""

from __future__ import annotations

import threading
import time

from as_platform.capacity_harness import CapacityDriver, CapacityLevelResult


def test_driver_runs_each_requested_level() -> None:
    """The driver produces exactly one result per requested level."""
    driver = CapacityDriver(call_factory=lambda: None, levels=[1, 2, 3])
    results = driver.run()
    assert len(results) == 3
    assert [r.level for r in results] == [1, 2, 3]


def test_driver_default_levels_cover_expected_range() -> None:
    """Default levels increase geometrically: [1, 4, 8, 16, 32, 64, 128]."""
    driver = CapacityDriver(call_factory=lambda: None)
    assert driver.levels == [1, 4, 8, 16, 32, 64, 128]


def test_driver_result_counts_completed_calls() -> None:
    """With a factory that always returns, all calls complete and none time out."""
    driver = CapacityDriver(call_factory=lambda: None, levels=[5], timeout=1.0)
    results = driver.run()
    assert len(results) == 1
    r = results[0]
    assert r.level == 5
    assert r.completed == 5
    assert r.timed_out == 0


def test_driver_times_out_slow_calls() -> None:
    """A factory that sleeps longer than timeout produces timed-out calls."""
    driver = CapacityDriver(call_factory=lambda: time.sleep(0.3), levels=[3], timeout=0.05)
    results = driver.run()
    r = results[0]
    assert r.level == 3
    # All three should time out since 0.3s > 0.05s timeout
    assert r.timed_out == 3
    assert r.completed == 0


def test_driver_result_carries_loop_gap_field() -> None:
    """CapacityLevelResult exposes all three observation fields."""
    driver = CapacityDriver(call_factory=lambda: None, levels=[1])
    results = driver.run()
    r = results[0]
    assert isinstance(r, CapacityLevelResult)
    assert isinstance(r.level, int)
    assert isinstance(r.completed, int)
    assert isinstance(r.timed_out, int)
    # loop_gap_ms can be None (no running sippy loop) or a float
    assert r.loop_gap_ms is None or isinstance(r.loop_gap_ms, float)


def test_driver_levels_run_independently() -> None:
    """Each level is driven and observed independently — one level failing doesn't break another."""
    counter = {"called": 0, "errors": 0}

    def factory() -> None:
        counter["called"] += 1
        if counter["called"] <= 2:
            # First two calls are slow (will time out)
            time.sleep(1.0)

    driver = CapacityDriver(call_factory=factory, levels=[2, 2], timeout=0.05)
    results = driver.run()
    assert len(results) == 2
    # Both levels produced results even though factory was slow on first calls
    assert all(isinstance(r, CapacityLevelResult) for r in results)
    # Total calls driven == 2 + 2 = 4
    assert counter["called"] == 4


def test_harness_uses_barrier_to_start_all_workers_at_once() -> None:
    """Workers in the same level are released simultaneously by a barrier."""
    start_times: list[float] = []
    lock = threading.Lock()

    def factory() -> None:
        with lock:
            start_times.append(time.monotonic())
        time.sleep(0.01)

    driver = CapacityDriver(call_factory=factory, levels=[10], timeout=1.0)
    driver.run()

    # All start times should be very close — within 50ms of each other
    if len(start_times) >= 2:
        spread = max(start_times) - min(start_times)
        # Barrier-based start is nearly simultaneous; spread < 100ms is plenty
        assert spread < 0.1, f"Worker start spread was {spread:.3f}s, expected < 0.1s"
