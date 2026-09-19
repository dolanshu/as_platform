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

"""The transport seam: the local socket the AS stack binds and sends on.

:class:`Transport` is the boundary and :class:`UdpTransport` its only implementation in
P10 — the existing behaviour (ADR-0003). The stack asks its transport for the sippy
``global_config`` keys that describe the binding rather than spelling them out itself, so a
second transport contributes the keys it needs without the stack learning which one it
holds (ADR-0009 decision 5, ``docs/architecture/lld.md`` section 11.4).

P10 defines the boundary and stops there (REQ-NF-020, D9): no second transport, no TLS, and
no registry or factory that selects one. P11 adds the second class and, only then, the
selection.
"""

from __future__ import annotations

from typing import Any, Protocol

__all__ = ["Transport", "UdpTransport"]


class Transport(Protocol):
    """The local socket the stack binds and sends on.

    Attributes:
        address: Local bind address.
        port: Local bind port.
    """

    #: Local bind address.
    address: str

    #: Local bind port.
    port: int

    def sip_config(self) -> dict[str, Any]:
        """Return the sippy ``global_config`` keys that describe this binding.

        Returns:
            The ``global_config`` entries this transport contributes — for UDP the local
            address and port.
        """
        ...


class UdpTransport:
    """The UDP trunk binding: the only transport in this POC (ADR-0003).

    Attributes:
        address: Local bind address.
        port: Local bind port.
    """

    def __init__(self, address: str, port: int) -> None:
        """Create the UDP binding without opening a socket.

        Args:
            address: Local bind address.
            port: Local UDP port.
        """
        self.address = address
        self.port = port

    def sip_config(self) -> dict[str, Any]:
        """Return the sippy ``global_config`` keys for this UDP binding.

        Returns:
            ``_sip_address`` and ``_sip_port``, exactly the keys the stack built inline
            before the seam existed.
        """
        return {"_sip_address": self.address, "_sip_port": self.port}
