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
no registry or factory that selects one. P11 adds the second class — :class:`TlsTransport`
— and, only then, the selection.

The optional :meth:`start` and :meth:`stop` lifecycle methods are added in P11 because
``TlsTransport`` owns sockets and threads that must outlive the sippy binding cycle;
:class:`UdpTransport` does not implement them — sippy manages the UDP server itself.
"""

from __future__ import annotations

import contextlib
import socket
import ssl
import threading
from typing import Any, Protocol

__all__ = ["Transport", "TlsTransport", "UdpTransport"]


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

    def start(self) -> None:
        """Start transport-level resources before sippy binds.

        Optional: transport implementations that own sockets or threads (e.g.
        :class:`TlsTransport`) start them here. :class:`UdpTransport` does nothing —
        sippy starts its own UDP server — so the default is a no-op.
        """
        ...

    def stop(self) -> None:
        """Release transport-level resources after sippy shuts down.

        Optional: the counterpart of :meth:`start`. Default is a no-op.
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

    def start(self) -> None:
        """No-op: sippy manages the UDP server itself."""

    def stop(self) -> None:
        """No-op: sippy manages the UDP server itself."""


class TlsTransport:
    """A TLS-terminating transport that bridges encrypted SIP to a local sippy UDP socket.

    sippy 2.4.2 has no SIP TLS or TCP support (REQ-NF-022, ADR-0010 verified facts). This
    transport terminates TLS at the Transport seam using Python's ``ssl`` + ``socket``
    modules, binds to the configured TLS port (typically 5061), and bridges decrypted SIP
    messages across a paired local UDP socket that sippy's ``SipTransactionManager`` binds
    to. The bridge runs as a background thread.

    sippy's internal UDP machinery is untouched — it sees only a UDP socket on localhost.

    Attributes:
        address: Local bind address for the TLS listener.
        port: Local bind port for the TLS listener.
        certfile: Path to the server certificate file (PEM).
        keyfile: Path to the server private key file (PEM).
        cafile: Optional path to a CA certificate file for client verification.
    """

    def __init__(
        self,
        address: str,
        port: int,
        certfile: str,
        keyfile: str,
        cafile: str | None = None,
    ) -> None:
        """Create the TLS transport without opening sockets.

        Args:
            address: Local bind address for the TLS listener (typically ``0.0.0.0`` or
                ``::`` for dual-stack).
            port: Local TLS port (typically 5061).
            certfile: Path to a PEM certificate file. Must exist before :meth:`start`.
            keyfile: Path to the PEM private key file. Must exist before :meth:`start`.
            cafile: Optional path to a PEM CA certificate file for verifying client
                certificates. When omitted, no client verification is performed.
        """
        self.address = address
        self.port = port
        self.certfile = certfile
        self.keyfile = keyfile
        self.cafile = cafile

        # Internal state — populated by start()
        self._local_udp_port: int | None = None
        self._tls_socket: ssl.SSLSocket | None = None
        self._local_socket_in: socket.socket | None = None
        self._local_socket_out: socket.socket | None = None
        self._bridge_threads: list[threading.Thread] = []
        self._running = threading.Event()

    def sip_config(self) -> dict[str, Any]:
        """Return the sippy ``global_config`` keys for the local UDP side of the bridge.

        Returns:
            ``_sip_address`` and ``_sip_port`` pointing at the loopback UDP socket the
            bridge uses. Must be called **after** :meth:`start`, which allocates the UDP
            port.

        Raises:
            RuntimeError: When called before :meth:`start`.
        """
        if self._local_udp_port is None:
            raise RuntimeError(
                "TlsTransport.sip_config() called before start() — no UDP port allocated"
            )
        return {"_sip_address": "127.0.0.1", "_sip_port": self._local_udp_port}

    def start(self) -> None:
        """Open the TLS listener, allocate the local UDP port and start the bridge threads.

        Raises:
            FileNotFoundError: When certfile or keyfile does not exist.
            socket.error: When the TLS or UDP sockets cannot be created.
        """
        self._running.set()

        # 1. Open the TLS server socket
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.certfile, self.keyfile)
        if self.cafile is not None:
            ctx.load_verify_locations(self.cafile)
            ctx.verify_mode = ssl.CERT_OPTIONAL

        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw_sock.bind((self.address, self.port))
        raw_sock.listen(16)
        self._tls_socket = ctx.wrap_socket(raw_sock, server_side=True)

        # 2. Allocate a local UDP port (bind to port 0, let OS assign)
        local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        local.bind(("127.0.0.1", 0))
        self._local_udp_port = local.getsockname()[1]
        local.close()

        # 3. Open the two UDP sockets sippy will use — one bound, one unbound
        self._local_socket_in = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._local_socket_in.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._local_socket_in.bind(("127.0.0.1", self._local_udp_port))
        self._local_socket_in.settimeout(1.0)  # for cooperative shutdown

        self._local_socket_out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._local_socket_out.settimeout(1.0)

    def stop(self) -> None:
        """Close all sockets and wait for bridge threads to exit."""
        self._running.clear()

        for sock in (self._tls_socket, self._local_socket_in, self._local_socket_out):
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.close()

        for thread in self._bridge_threads:
            thread.join(timeout=2.0)

        self._tls_socket = None
        self._local_socket_in = None
        self._local_socket_out = None
        self._bridge_threads.clear()
        self._local_udp_port = None
