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

"""Shared platform skeleton for the third-party IMS/SIP Application Server instances.

The library carries the parts of the two AS instances that are not tied to a use case:
the sippy adapter boundary, the error-model mechanism, the observability surface, the
bootstrap plumbing, the controller and stack shells, and the pluggable transport and
state-store seams. The number-translation and anti-fraud use cases stay in the reference
repository, which consumes this package from a sibling checkout.

The modules above move here over the staged extraction of ADR-0009 decision 7, one
dependency-ordered step at a time; step 2 moved the leaf modules and step 3 the version
chain and the bootstrap plumbing.
"""

from __future__ import annotations

from pathlib import Path

from as_platform.version import read_version, version_file_version

__all__ = ["__version__"]

#: Distribution name declared in ``pyproject.toml``. An installed wheel carries its version
#: in the distribution metadata under exactly this name.
_DISTRIBUTION_NAME = "as-platform"

#: Reported when neither the installed distribution metadata nor the ``VERSION`` file yields
#: a version.
_UNKNOWN_VERSION = "0.0.0+unknown"

#: Repository ``VERSION`` file, two directories above this module: the version source of a
#: source checkout.
_VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"


def _version_file_version() -> str:
    """Return the repository version from the ``VERSION`` file.

    Returns:
        The stripped contents of ``VERSION``, or ``_UNKNOWN_VERSION`` when the file is not
        present — which is the case for an installed wheel.
    """
    return version_file_version(_VERSION_FILE, _UNKNOWN_VERSION)


__version__ = read_version(_DISTRIBUTION_NAME, _version_file_version)
