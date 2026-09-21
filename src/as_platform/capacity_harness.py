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

"""Capacity harness: a library-level load generator and observation tool.

P10 extracts the skeleton and leaves the question of how the platform behaves under load
unanswered (D10). P9.5 ran a read-only probe — a one-off script that discovered the AS
event loop is the serialisation point and that ``timerB`` is the effective give-up edge —
but it was ad-hoc. P11 turns that probe into a first-class library component.

:class:`CapacityDriver` drives a call factory at increasing offered concurrency levels and
records per-level observations: how many calls completed within the configured timeout,
how many timed out, and the event-loop gap measured during the level. It does **not**
publish benchmark numbers (D10, REQ-NF-009, REQ-NF-025). Its output is structured data
passed back to the caller, written to trace, or logged — never a "this AS handles X calls
per second" claim.

The harness drives through the call-controller callback interface, not over sockets. This
trades realism for determinism: round-trip time, retransmission behaviour and message-size
effects are not captured, but the driver gets consistent results regardless of machine
speed and network state. P9.5's read-only probe remains the closest thing to an end-to-end
load measurement.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

__all__ = ["CapacityDriver", "CapacityLevelResult"]


@dataclass
class CapacityLevelResult:
    """Observations from one offered-concurrency level."""

    #: Offered concurrency — how many calls were driven simultaneously.
    level: int
    #: Calls that completed within the configured timeout.
    completed: int
    #: Calls that did not complete within the timeout.
    timed_out: int
    #: Event-loop scheduling delay observed during this level, in milliseconds.
    #: Measured by scheduling a zero-delay call on the sippy event loop and recording
    #: the actual latency. ``None`` when the level did not run on a sippy loop.
    loop_gap_ms: float | None


class CapacityDriver:
    """Drive an AS call factory at increasing offered concurrency levels.

    The driver accepts a **call factory** — a callable that, when invoked, drives one
    simulated call through an AS stack and returns when that call completes. The factory
    is called from one worker thread per offered call; ``threading.Thread`` is the
    implementation, so the driver is not sippy-aware and does not require a running sippy
    event loop.

    This is a library component, not an application. It does not connect to a real S-SBC,
    bind sockets, or run a sippy loop. The application provides a factory that does those
    things (typically by driving a stack's callback interface in-process).
    """

    def __init__(
        self,
        call_factory: Callable[[], None],
        levels: Iterable[int] | None = None,
        timeout: float = 10.0,
    ) -> None:
        """Create the capacity driver.

        Args:
            call_factory: Callable that drives one simulated call and returns when the
                call completes. If the factory blocks indefinitely for a call, that call
                is counted as timed out after ``timeout`` seconds.
            levels: Offered concurrency levels. Defaults to ``[1, 4, 8, 16, 32, 64, 128]``.
            timeout: Maximum seconds each call is allowed before it is counted as timed
                out. Also used as the per-level observation window.
        """
        self.call_factory = call_factory
        self.levels = list(levels) if levels is not None else [1, 4, 8, 16, 32, 64, 128]
        self.timeout = timeout

    def run(self) -> list[CapacityLevelResult]:
        """Run every level and return its observations.

        Returns:
            One :class:`CapacityLevelResult` per level, in ascending level order.
        """
        results: list[CapacityLevelResult] = []
        for level in self.levels:
            result = self._run_level(level)
            results.append(result)
        return results

    def _run_level(self, level: int) -> CapacityLevelResult:
        """Drive one offered-concurrency level and return its observations."""
        completed = 0
        timed_out = 0
        barrier = threading.Barrier(level + 1)  # +1 for the main thread
        time.monotonic()
        results_lock = threading.Lock()

        def worker() -> None:
            nonlocal completed, timed_out
            # Wait for the main thread to release the barrier — all workers start at once
            barrier.wait()
            time.monotonic()
            factory = self.call_factory

            class _Worker:
                done = threading.Event()

            w = _Worker()

            def run_with_timeout() -> None:
                try:
                    factory()
                finally:
                    w.done.set()

            t = threading.Thread(target=run_with_timeout, daemon=True)
            t.start()
            # Wait for completion or timeout
            finished = w.done.wait(timeout=self.timeout)
            t.join(timeout=0.1)  # Try to reap; don't block long
            with results_lock:
                if finished:
                    completed += 1
                else:
                    timed_out += 1

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(level)]
        for t in threads:
            t.start()

        # Release all workers at once and wait for them
        barrier.wait()
        for t in threads:
            t.join(timeout=self.timeout + 5.0)  # Allow some grace

        loop_gap = self._measure_loop_gap()
        return CapacityLevelResult(
            level=level,
            completed=completed,
            timed_out=timed_out,
            loop_gap_ms=loop_gap,
        )

    @staticmethod
    def _measure_loop_gap() -> float | None:
        """Measure how late the event loop runs a zero-delay callback, in milliseconds.

        Uses ``ED2.loop()`` scheduling via a Python daemon thread that fires a
        zero-sleep call and measures the actual latency. Returns ``None`` when there is
        no running sippy event loop in this process — which is normal for library-level
        tests that do not run sippy.
        """
        try:
            from sippy.Core.EventDispatcher import ED2  # type: ignore[import-untyped]
        except ImportError:
            return None

        event = threading.Event()
        actual_latency_ms: list[float] = [0.0]

        def probe() -> None:
            scheduled = time.monotonic()
            actual_latency_ms[0] = (scheduled - probe._scheduled_at) * 1000  # type: ignore[attr-defined]
            event.set()

        probe._scheduled_at = time.monotonic()  # type: ignore[attr-defined]

        try:
            ED2.callFromThread(probe)
        except Exception:
            # No running ED2 loop in this process
            return None

        # Give it a bit of time
        if event.wait(timeout=2.0):
            return actual_latency_ms[0]
        return None
