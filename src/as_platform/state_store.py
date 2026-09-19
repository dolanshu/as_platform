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
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Protocol

__all__ = ["InMemoryStateStore", "StateStore"]


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
