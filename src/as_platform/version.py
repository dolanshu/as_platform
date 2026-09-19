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

"""Runtime version discovery: installed distribution metadata first, then a ``VERSION`` file.

The chain is the same for the library and for every consumer, so it lives with the
skeleton (ADR-0009 decision 2). Metadata comes first because it is the only branch an
installed wheel can satisfy: a wheel ships no ``VERSION`` file, so the metadata the build
backend wrote into it is the only version source it carries. The ``VERSION`` file is the
source-checkout path, where no distribution metadata has to exist at all. The unknown
placeholder is the last resort, reached only by a broken install.

This module carries the mechanism only; each package supplies its own distribution name,
its own ``VERSION`` path and its own placeholder, so nothing here imports an application
package (REQ-F-030).
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from pathlib import Path

__all__ = ["distribution_version", "read_version", "version_file_version"]


def distribution_version(distribution_name: str) -> str:
    """Return the version recorded in the installed distribution metadata.

    Args:
        distribution_name: Distribution name as declared in ``pyproject.toml``.

    Returns:
        The version string of the installed distribution.

    Raises:
        importlib.metadata.PackageNotFoundError: The distribution is not installed — the
            case for a source checkout that was never installed.
        Exception: Any error a metadata backend raises while reading a present but
            unreadable distribution is propagated as well and handled by the caller.
    """
    return importlib.metadata.version(distribution_name)


def version_file_version(version_file: Path, unknown: str) -> str:
    """Return the version recorded in a repository ``VERSION`` file.

    Args:
        version_file: Path to the ``VERSION`` file.
        unknown: Placeholder returned when the file is not present.

    Returns:
        The stripped contents of the file, or ``unknown`` when it cannot be read — which
        is the case for an installed wheel.
    """
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return unknown


def read_version(distribution_name: str, version_file_reader: Callable[[], str]) -> str:
    """Return the runtime version: installed metadata first, then the ``VERSION`` file.

    The three-step chain (P5, the "Version discovery" gap of ``docs/production-gaps.md``):

    1. ``importlib.metadata.version(distribution_name)`` — the version an installed wheel
       carries in its distribution metadata;
    2. ``version_file_reader()`` — the repository ``VERSION`` file, the source-checkout
       path, where no distribution metadata has to exist at all;
    3. whatever the reader returns as its unknown placeholder when neither source is
       available.

    Metadata comes first because it is the only branch an installed wheel can satisfy: a
    wheel ships no ``VERSION`` file. It is also safe for an editable install and for a bare
    source checkout, because ``VERSION``, ``pyproject.toml`` and the installed metadata all
    carry the same version — the version-file test guards the first pair, and an editable
    install derives its metadata from the same ``pyproject.toml``.

    Args:
        distribution_name: Distribution name to read metadata for.
        version_file_reader: Callable returning the ``VERSION`` file's version, or its
            package's unknown placeholder when the file is absent.

    Returns:
        The resolved version string, or the placeholder when no source is available.
    """
    try:
        return distribution_version(distribution_name)
    except Exception:  # PackageNotFoundError, or any metadata backend failure.
        return version_file_reader()
