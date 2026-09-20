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

"""Tests for the distribution -> VERSION version chain (ADR-0009 decision 2)."""

from __future__ import annotations

from pathlib import Path

import as_platform
from as_platform.version import read_version, version_file_version


def test_the_installed_distribution_metadata_reports_the_library_version() -> None:
    """The checkout is installed, so the metadata branch resolves to 0.1.0."""
    assert as_platform.__version__ == "0.1.0"


def test_version_file_version_strips_the_file_contents(tmp_path: Path) -> None:
    """A VERSION file's trailing newline is not part of the version."""
    version_file = tmp_path / "VERSION"
    version_file.write_text("9.9.9\n", encoding="utf-8")
    assert version_file_version(version_file, "0.0.0+unknown") == "9.9.9"


def test_version_file_version_falls_back_when_the_file_is_absent(tmp_path: Path) -> None:
    """An installed wheel ships no VERSION file, so the placeholder is returned."""
    assert version_file_version(tmp_path / "absent", "0.0.0+unknown") == "0.0.0+unknown"


def test_read_version_prefers_the_distribution_metadata() -> None:
    """Metadata is the only branch an installed wheel can satisfy, so it comes first."""
    assert read_version("as-platform", lambda: "from-file") == "0.1.0"


def test_read_version_falls_back_to_the_version_file() -> None:
    """An unknown distribution falls through to the VERSION file reader."""
    assert read_version("no-such-distribution-xyz", lambda: "from-file") == "from-file"
