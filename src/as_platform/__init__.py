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

Step 1 of the staged extraction (ADR-0009 decision 7) creates the repository and an empty,
typed, importable package; the modules above move here in the following steps.
"""

from __future__ import annotations
