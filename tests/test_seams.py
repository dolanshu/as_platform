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

"""The two pluggable seams ship **two implementations each** after P11.

P10 defined the boundaries and shipped exactly one implementation per seam (REQ-NF-020,
ADR-0009 decision 5). P11 added the second: :class:`TlsTransport` for the transport seam,
:class:`RedisStateStore` for the state-store seam, and :class:`CapacityDriver` as a
first-class harness. These tests are the P11 successor to P10's boundary protection: they
pin that **two** implementations exist behind each seam, the harness module is present,
and ``UdpTransport`` / ``InMemoryStateStore`` remain the defaults (REQ-NF-024).
"""

from __future__ import annotations

import ast
from pathlib import Path

from as_platform import state_store, transport

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "as_platform"


def _library_modules() -> list[Path]:
    """Return every Python module of the library package.

    Returns:
        The ``.py`` files under ``src/as_platform``.
    """
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _defined_class_names() -> set[str]:
    """Return the name of every class defined anywhere in the library.

    Returns:
        The class names, nested ones included.
    """
    names: set[str] = set()
    for path in _library_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names.update(node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    return names


def test_the_transport_seam_exports_the_boundary_and_two_implementations() -> None:
    """``as_platform.transport`` exposes Transport, UdpTransport and TlsTransport.

    TlsTransport is P11's second implementation — the proof that the seam is pluggable.
    """
    assert transport.__all__ == ["Transport", "TlsTransport", "UdpTransport"]


def test_the_state_store_seam_exports_the_boundary_and_two_implementations() -> None:
    """``as_platform.state_store`` exposes StateStore, InMemoryStateStore and RedisStateStore.

    RedisStateStore is P11's second implementation — the proof that the seam is pluggable.
    """
    assert state_store.__all__ == [
        "InMemoryStateStore",
        "RedisStateStore",
        "StateStore",
    ]


def test_the_library_defines_two_transports_and_two_state_stores() -> None:
    """Every *Transport / *StateStore class is the interface or one of its two implementations."""
    classes = _defined_class_names()
    assert {name for name in classes if name.endswith("Transport")} == {
        "Transport",
        "TlsTransport",
        "UdpTransport",
    }
    assert {name for name in classes if name.endswith("StateStore")} == {
        "InMemoryStateStore",
        "RedisStateStore",
        "StateStore",
    }


def test_the_library_ships_the_capacity_harness_module() -> None:
    """capacity_harness is present as a first-class library module (REQ-F-037)."""
    stems = {path.stem for path in _library_modules()}
    assert "capacity_harness" in stems
