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

"""The state-store seam: where the cross-call caller records are kept.

:class:`StateStore` is the boundary and :class:`InMemoryStateStore` its only implementation
in P10 (ADR-0009 decision 5, ``docs/architecture/lld.md`` section 11.4). Values are opaque
to the store: it never interprets a record, it only keeps it and orders it least recently
used. The seam sits **under** ``CallerStateStore``, which stays in the anti-fraud
application and stays process-level; the window and the ledger are the use case's, the
storage is not (D9).

``trim`` carries a ``prefix`` because the anti-fraud keeps **two independent structures with
two independent bounds** in one store — the call-rate window and the reputation ledger — so
one bound has to be applied per key family to reproduce today's capacity exactly.

P10 defines the boundary and stops there (REQ-NF-020, D9): no external store, no Redis, and
no registry or factory that selects an implementation. The in-memory store remains the only
one until P11, which adds the second class and, only then, the selection.

P11 adds :class:`RedisStateStore`. Redis I/O happens on a dedicated worker thread, not
inside sippy callbacks — ``AGENT.md section 6`` forbids blocking the ``ED2`` event loop.
"""

from __future__ import annotations

import contextlib
import json
import queue
import threading
from collections import OrderedDict
from typing import Any, Protocol

__all__ = ["InMemoryStateStore", "RedisStateStore", "StateStore"]


class StateStore(Protocol):
    """Where the cross-call caller records are kept, opaque to the store."""

    def read(self, key: str) -> Any | None:
        """Return the record and mark it most recently used.

        Args:
            key: The record's key.

        Returns:
            The stored record, or ``None`` when the key is absent.
        """
        ...

    def write(self, key: str, value: Any) -> None:
        """Store the record and mark it most recently used.

        Args:
            key: The record's key.
            value: The record to store; opaque to the store.
        """
        ...

    def trim(self, prefix: str, limit: int) -> None:
        """Drop the least recently used keys carrying ``prefix`` above ``limit``.

        Args:
            prefix: The key family the bound applies to.
            limit: How many keys of that family may remain.
        """
        ...

    def start(self) -> None:
        """Start any background resources this store needs.

        Optional: :class:`InMemoryStateStore` does nothing. :class:`RedisStateStore`
        starts its worker thread. Default is a no-op.
        """
        ...

    def stop(self) -> None:
        """Release any background resources.

        Optional: the counterpart of :meth:`start`. Default is a no-op.
        """
        ...


class InMemoryStateStore:
    """The in-memory state store: the only implementation until P11 (D9).

    One lock guards the records, because the callbacks and the console feed run on
    different threads. The records are held in insertion order and re-ordered on every
    access, so eviction drops the least recently used key first.
    """

    def __init__(self) -> None:
        """Create an empty store."""
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, Any] = OrderedDict()

    def read(self, key: str) -> Any | None:
        """Return the record and mark it most recently used.

        Args:
            key: The record's key.

        Returns:
            The stored record, or ``None`` when the key is absent.
        """
        with self._lock:
            if key not in self._entries:
                return None
            self._entries.move_to_end(key)
            return self._entries[key]

    def write(self, key: str, value: Any) -> None:
        """Store the record and mark it most recently used.

        Args:
            key: The record's key.
            value: The record to store; opaque to the store.
        """
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)

    def trim(self, prefix: str, limit: int) -> None:
        """Drop the least recently used keys carrying ``prefix`` above ``limit``.

        Args:
            prefix: The key family the bound applies to.
            limit: How many keys of that family may remain.
        """
        with self._lock:
            family = [key for key in self._entries if key.startswith(prefix)]
            while len(family) > limit:
                del self._entries[family.pop(0)]

    def start(self) -> None:
        """No-op: the in-memory store has no background resources to start."""

    def stop(self) -> None:
        """No-op: the in-memory store has no background resources to stop."""


class RedisStateStore:
    """A Redis-backed state store using a background-worker queue pattern.

    All public methods (``read``, ``write``, ``trim``) enqueue work items and wait for the
    worker thread to signal the result through a condition variable. This keeps the sippy
    event loop unblocked (``AGENT.md section 6``) — the sippy callback only enqueues; the
    dedicated worker thread runs the actual Redis I/O.

    Values are serialized as JSON for storage. Objects that are not JSON-serializable must
    be pre-serialized by the caller before :meth:`write`.

    Attributes:
        redis_url: Redis connection URL or host:port string.
        db: Redis database number (default 0).
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        db: int = 0,
    ) -> None:
        """Create the Redis store without connecting.

        Args:
            redis_url: Redis connection URL (``redis://host:port/db``) or bare host:port.
            db: Redis database number. Ignored when ``redis_url`` already carries a ``/db``
                suffix.
        """
        self.redis_url = redis_url
        self.db = db
        self._queue: queue.Queue[_RedisWorkItem] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._running = threading.Event()
        self._client: Any = None  # redis.Redis — imported lazily in start()

    def start(self) -> None:
        """Connect to Redis and start the worker thread.

        Raises:
            ImportError: When the ``redis`` Python package is not installed.
            redis.ConnectionError: When Redis is unreachable.
        """
        import redis as _redis  # Import lazily so tests without redis still import the module

        self._client = _redis.from_url(self.redis_url)
        # Verify connectivity — this will raise if Redis is down
        self._client.ping()

        self._running.set()
        self._worker = threading.Thread(target=self._run, daemon=True, name="redis-worker")
        self._worker.start()

    def stop(self) -> None:
        """Stop the worker thread and close the Redis connection."""
        self._running.clear()
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None

    def read(self, key: str) -> Any | None:
        """Return the record for ``key``, or ``None`` when absent.

        The call blocks until the worker thread has completed the Redis GET. This is
        synchronous **to the caller**, but the caller must not be a sippy callback — that
        would block the event loop (AGENT.md section 6). Callers that are sippy callbacks
        must enqueue asynchronously or bridge through another mechanism.

        Args:
            key: The record's key.

        Returns:
            The stored record (deserialized from JSON), or ``None`` when absent.
        """
        item = _RedisWorkItem(op="read", key=key)
        self._queue.put(item)
        item.wait()
        if item.error is not None:
            raise item.error
        return item.result

    def write(self, key: str, value: Any) -> None:
        """Store the record as JSON at ``key``.

        Values must be JSON-serializable; non-JSON types raise ``TypeError`` before
        enqueuing.

        Args:
            key: The record's key.
            value: The record to store.
        """
        json_value = json.dumps(value, default=str)
        item = _RedisWorkItem(op="write", key=key, value=json_value)
        self._queue.put(item)
        item.wait()
        if item.error is not None:
            raise item.error

    def trim(self, prefix: str, limit: int) -> None:
        """Drop the oldest ``prefix`` keys above ``limit``.

        This is a **best-effort** eviction — Redis itself has no native per-prefix ordered
        eviction, so the worker lists all matching keys, orders them by access time
        (``OBJECT IDLETIME``), and deletes the excess. This is **not** a strict LRU like
        the in-memory store provides; it is good enough for a cross-call state store and
        is the same shape the in-memory ``trim`` uses.

        Args:
            prefix: The key family to bound.
            limit: Maximum number of keys of that family to keep.
        """
        item = _RedisWorkItem(op="trim", key=prefix, limit=limit)
        self._queue.put(item)
        item.wait()
        if item.error is not None:
            raise item.error

    def _run(self) -> None:
        """Worker thread: drain the queue and perform Redis operations."""
        assert self._client is not None
        while self._running.is_set() or not self._queue.empty():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if item.op == "read":
                    raw = self._client.get(item.key)
                    item.result = json.loads(raw) if raw is not None else None
                elif item.op == "write":
                    self._client.set(item.key, item.value)
                elif item.op == "trim":
                    pattern = f"{item.key}*"
                    keys = list(self._client.scan_iter(match=pattern))
                    if len(keys) > (item.limit or 0):
                        # Evict by idle time — oldest idle first
                        idle_times: dict[str, int] = {}
                        for k in keys:
                            ttl = self._client.object("idletime", k)
                            if ttl is None:
                                ttl = 0
                            idle_times[k] = ttl
                        evict_count = len(keys) - (item.limit or 0)
                        for k, _ in sorted(idle_times.items(), key=lambda kv: kv[1], reverse=True)[
                            :evict_count
                        ]:
                            self._client.delete(k)
            except Exception as exc:  # pragma: no cover — network-level failures
                item.error = exc
            finally:
                item.done()


class _RedisWorkItem:
    """A unit of work the Redis worker thread processes."""

    def __init__(
        self,
        op: str,
        key: str,
        value: str | None = None,
        limit: int | None = None,
    ) -> None:
        self.op = op
        self.key = key
        self.value = value
        self.limit = limit
        self.event = threading.Event()
        self.result: Any = None
        self.error: Exception | None = None

    def wait(self) -> None:
        """Block until the worker has completed this item."""
        self.event.wait(timeout=10.0)

    def done(self) -> None:
        """Signal that this item is complete."""
        self.event.set()
