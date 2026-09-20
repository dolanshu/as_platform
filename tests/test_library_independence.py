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

"""The library-level independence assertion of REQ-F-030.

This module walks every ``.py`` file of the library, parses it with :mod:`ast` and
collects the top-level names each module imports, then asserts that none of them is an
application package. It parses the source rather than grepping it, so a comment or a
string that merely mentions ``as_app`` cannot fool the guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "as_platform"

APPLICATION_PACKAGES = frozenset({"as_app", "anti_fraud_as"})


def _imported_top_level_modules(tree: ast.AST) -> set[str]:
    """Return the top-level module names imported by a parsed module.

    Args:
        tree: The parsed module.

    Returns:
        The first dotted component of every ``import`` and ``from ... import`` target,
        including imports nested inside functions and classes.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _library_modules() -> list[Path]:
    """Return every Python module of the library package.

    Returns:
        The ``.py`` files under ``src/as_platform``.
    """
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def test_the_library_package_has_modules_to_check() -> None:
    """The guard would be vacuous if the walk found nothing."""
    assert _library_modules()


def test_library_does_not_import_the_application_packages() -> None:
    """REQ-F-030: no module under ``src/as_platform`` imports ``as_app`` or ``anti_fraud_as``."""
    offenders: dict[str, set[str]] = {}
    for path in _library_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        forbidden = _imported_top_level_modules(tree) & APPLICATION_PACKAGES
        if forbidden:
            offenders[str(path.relative_to(PACKAGE_ROOT))] = forbidden
    assert not offenders, f"library modules import an application package: {offenders}"
