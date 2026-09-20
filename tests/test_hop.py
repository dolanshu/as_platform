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

"""Tests for the NextHop value object shared by the adapter and the controller shell."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from as_platform.hop import NextHop


def test_next_hop_defaults_fill_port_transport_priority_and_description() -> None:
    """A hop only has to name an address; the rest has documented defaults."""
    hop = NextHop(name="s-sbc", address="127.0.0.1")
    assert hop.port == 5060
    assert hop.transport == "udp"
    assert hop.priority == 1
    assert hop.description == ""


def test_next_hop_rejects_unknown_fields() -> None:
    """The model forbids extras, so a typo in the catalogue is a load error."""
    with pytest.raises(ValidationError):
        NextHop(name="s-sbc", address="127.0.0.1", tls=True)  # type: ignore[call-arg]


def test_next_hop_rejects_a_port_out_of_range() -> None:
    """The UDP port is bounded to the valid range."""
    with pytest.raises(ValidationError):
        NextHop(name="s-sbc", address="127.0.0.1", port=0)
