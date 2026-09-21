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

"""The next-hop value object a B2BUA relays towards.

A B2BUA always relays towards an ordered list of next hops, so the value object is
skeleton, not use-case specific: :mod:`as_platform.sip_adapter` and the call controller
both need it, and it lives in its own module so neither has to import the other
(ADR-0009 decision 2). The catalogue that *produces* the list — the routing document, its
schema and the translation — stays in the number-translation application.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["NextHop"]


class NextHop(BaseModel):
    """A next hop the AS may originate the outbound INVITE towards.

    Attributes:
        name: Configuration key referenced by routing rules.
        address: IP address or FQDN of the peer.
        port: SIP port of the peer (default 5060 for UDP, 5061 for TLS).
        transport: Transport the B2BUA uses to reach this peer; ``udp`` is the default
            (ADR-0003), ``tls`` is added in P11 (ADR-0010 decision 2).
        priority: Selection order, lower wins. Equal priorities keep file order.
        description: What this peer is, for the console.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    address: str
    port: int = Field(default=5060, ge=1, le=65535)
    transport: Literal["udp", "tls"] = "udp"
    priority: int = Field(default=1, ge=1)
    description: str = ""
