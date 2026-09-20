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

"""The library's document set and its own gate (REQ-NF-019, REQ-NF-021).

The new repository is a library, not a service, so it follows the library standard of D8:
exactly the three library documents — an API reference, an integration guide and a
compatibility matrix — and a gate of its own (``ruff`` format and lint, ``mypy``,
``pytest``). It deliberately does not copy the reference implementation's application
document set: the operations set (deployment, runbook, troubleshooting) does not apply to a
library.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The library documentation standard of D8 (REQ-NF-019).
LIBRARY_DOCUMENTS = (
    "docs/api-reference.md",
    "docs/integration-guide.md",
    "docs/compatibility-matrix.md",
)

#: Application documents that a library must not carry (REQ-NF-019).
APPLICATION_DOCUMENTS = (
    "docs/deployment.md",
    "docs/runbook.md",
    "docs/troubleshooting.md",
)

#: The commands the library's lint target runs (REQ-NF-021).
LINT_COMMANDS = ("ruff format --check", "ruff check", "mypy")

#: The command the library's test target runs (REQ-NF-021).
TEST_COMMAND = "pytest"

#: The distribution name the library manifest must self-report (ADR-0009 decision 1).
LIBRARY_DISTRIBUTION = "as-platform"


def _project_table(pyproject: str) -> str:
    """Return the body of the ``[project]`` table of a ``pyproject.toml``.

    Args:
        pyproject: The manifest content.

    Returns:
        The lines between ``[project]`` and the next table header, or an empty string when
        the table is absent.
    """
    match = re.search(r"^\[project\]\n(.*?)(?=^\[|\Z)", pyproject, re.DOTALL | re.MULTILINE)
    return match.group(1) if match else ""


def _make_recipe(makefile: str, target: str) -> str:
    """Return the tab-indented recipe lines of one Makefile target.

    Args:
        makefile: The Makefile content.
        target: The target name, without its colon.

    Returns:
        The recipe lines of the target, or an empty string when it is absent.
    """
    match = re.search(rf"^{re.escape(target)}:[^\n]*\n((?:\t[^\n]*\n)+)", makefile, re.MULTILINE)
    return match.group(1) if match else ""


def _ci_run_commands(workflow: str) -> str:
    """Return the shell commands a GitHub Actions workflow runs.

    Args:
        workflow: The workflow file content.

    Returns:
        The ``run:`` command lines, joined by newlines. Job and step names are not included,
        so a renamed step cannot pass the gate assertion.
    """
    return "\n".join(re.findall(r"^\s*run:\s*(.+)$", workflow, re.MULTILINE))


def test_the_three_library_documents_exist() -> None:
    """The API reference, the integration guide and the compatibility matrix are present."""
    for relative in LIBRARY_DOCUMENTS:
        assert (REPO_ROOT / relative).is_file(), relative


def test_the_application_document_set_is_not_copied() -> None:
    """The operations set of the application repository does not apply to a library."""
    for relative in APPLICATION_DOCUMENTS:
        assert not (REPO_ROOT / relative).exists(), f"a library must not carry {relative}"


def test_the_makefile_defines_the_library_gate() -> None:
    """``Makefile`` runs ruff, mypy and pytest, so the library is gated where it lives."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    lint = _make_recipe(makefile, "lint")
    test = _make_recipe(makefile, "test")
    assert lint, "the Makefile has no lint target with a recipe"
    assert test, "the Makefile has no test target with a recipe"
    for command in LINT_COMMANDS:
        assert command in lint, f"the lint target does not run `{command}`"
    assert TEST_COMMAND in test, f"the test target does not run `{TEST_COMMAND}`"


def test_ci_runs_the_library_gate() -> None:
    """The workflow runs the same gate on the runner, after a push (REQ-NF-021)."""
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    commands = _ci_run_commands(workflow)
    for command in LINT_COMMANDS:
        assert command in commands, f"CI does not run `{command}`"
    assert TEST_COMMAND in commands, f"CI does not run `{TEST_COMMAND}`"


def test_pyproject_configures_the_gate_tools() -> None:
    """ruff, mypy and pytest are configured in ``pyproject.toml`` (REQ-NF-021)."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for table in ("[tool.ruff]", "[tool.ruff.lint]", "[tool.mypy]", "[tool.pytest.ini_options]"):
        assert table in text, f"pyproject.toml has no {table} table"


def test_the_library_is_a_standalone_distribution_not_a_workspace_member() -> None:
    """The library is its own distribution, not a uv workspace member (REQ-NF-019, D8).

    REQ-NF-019 says the new repository is **not** a uv workspace monorepo, and LLD section
    11.1 states that neither manifest declares the other a ``[tool.uv.workspace]`` member.
    This asserts the library's own half: its manifest carries no workspace table and it
    self-reports the distribution name of ADR-0009 decision 1, so it is consumed from a
    sibling checkout through that repository's ``path`` source rather than being a directory
    of a shared workspace with one lock and one root.
    """
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.uv.workspace]" not in text, "the library must not be a uv workspace member"
    project = _project_table(text)
    assert re.search(rf'^name\s*=\s*"{re.escape(LIBRARY_DISTRIBUTION)}"', project, re.MULTILINE), (
        "the manifest does not self-report `as-platform` (ADR-0009 decision 1)"
    )
