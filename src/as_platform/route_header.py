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

"""Loose-route helpers for the outbound leg of a third-party AS.

On an IMS trunk the operator's S-SBC inserts a ``Route`` set on the INVITE it forwards to
the AS. When the AS originates back through the S-SBC, RFC 3261 section 8.1.1.1 sends the
request to the **top** ``Route`` entry. Catalogue or environment next-hop knobs name the
operator hop for failover and observability; the wire destination is taken from the trunk
``Route`` when it is present.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["parse_top_route_target"]

#: One Route entry: ``<sip:host:port;lr>`` or ``<sip:user@host;lr>``.
_ROUTE_ENTRY = re.compile(
    r"<\s*sips?:([^<>@;]+@)?([^<>@:;]+)(?::(\d+))?",
    re.IGNORECASE,
)


def _route_header_values(request: Any) -> list[str]:
    """Return the raw Route header field values from a sippy request.

    Args:
        request: The inbound trunk INVITE.

    Returns:
        Route header bodies in wire order.
    """
    getter = getattr(request, "getHFBodys", None)
    if getter is None:
        return []
    values: list[str] = []
    for body in getter("route"):
        if hasattr(body, "getCopy"):
            values.append(str(body.getCopy()))
        else:
            values.append(str(body))
    return values


def parse_top_route_target(
    request: Any | None,
    *,
    default_port: int = 5060,
) -> tuple[str, int] | None:
    """Return the host and port of the top Route entry on a trunk INVITE.

    Args:
        request: The inbound trunk INVITE, or ``None``.
        default_port: Port assumed when the Route URI omits an explicit port.

    Returns:
        ``(host, port)`` of the first Route entry, or ``None`` when no Route is present.
    """
    if request is None:
        return None
    for raw in _route_header_values(request):
        for part in raw.split(","):
            match = _ROUTE_ENTRY.search(part.strip())
            if match is None:
                continue
            host = match.group(2)
            port_text = match.group(3)
            port = int(port_text) if port_text else default_port
            return host, port
    return None
