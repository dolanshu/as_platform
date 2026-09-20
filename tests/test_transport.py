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

"""Tests for the transport seam and its only P10 implementation (ADR-0009 decision 5)."""

from __future__ import annotations

from as_platform.transport import UdpTransport


def test_udp_transport_sip_config_names_the_address_and_port() -> None:
    """The seam contributes exactly the two sippy keys the stack built inline before."""
    transport = UdpTransport("127.0.0.1", 5060)
    assert transport.sip_config() == {"_sip_address": "127.0.0.1", "_sip_port": 5060}


def test_udp_transport_keeps_the_binding_it_was_given() -> None:
    """The transport exposes the bind address and port the stack reports."""
    transport = UdpTransport("10.0.0.1", 15060)
    assert transport.address == "10.0.0.1"
    assert transport.port == 15060
