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

"""The two pluggable seams ship exactly one implementation each in P10 (REQ-NF-020).

ADR-0009 decision 5 defines the boundaries and stops there: ``Transport`` is satisfied by
``UdpTransport`` and ``StateStore`` by ``InMemoryStateStore``, and P10 builds no second
transport (TLS), no external store (Redis) and no capacity harness (D9, D10). These tests
pin that boundary against the library's own source, so adding the second implementation in
P11 is a deliberate change that updates this file rather than one that passes unnoticed.
"""

from __future__ import annotations

import ast
from pathlib import Path

from as_platform import state_store, transport

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "as_platform"

#: The P11-only module vocabulary P10 must not contain (ADR-0009 decision 5).
P11_ONLY_MODULE_TOKENS = frozenset({"tls", "redis", "harness"})


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


def test_the_transport_seam_exports_the_boundary_and_one_implementation() -> None:
    """``as_platform.transport`` exposes the interface and its only P10 implementation."""
    assert transport.__all__ == ["Transport", "UdpTransport"]


def test_the_state_store_seam_exports_the_boundary_and_one_implementation() -> None:
    """``as_platform.state_store`` exposes the interface and its only P10 implementation."""
    assert state_store.__all__ == ["InMemoryStateStore", "StateStore"]


def test_the_library_defines_no_second_transport_and_no_second_state_store() -> None:
    """Every ``*Transport`` / ``*StateStore`` class is the interface or its one P10 class."""
    classes = _defined_class_names()
    assert {name for name in classes if name.endswith("Transport")} == {
        "Transport",
        "UdpTransport",
    }
    assert {name for name in classes if name.endswith("StateStore")} == {
        "InMemoryStateStore",
        "StateStore",
    }


def test_the_library_ships_no_tls_no_redis_and_no_capacity_harness() -> None:
    """No library module is named for a second transport, an external store or a harness."""
    offenders = sorted(
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in _library_modules()
        if P11_ONLY_MODULE_TOKENS & set(path.stem.split("_"))
    )
    assert not offenders, f"a P11-only module is present in P10: {offenders}"
