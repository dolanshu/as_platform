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

"""Process plumbing shared by every AS instance: port probing and graceful shutdown.

This is the use-case-agnostic half of an application's ``bootstrap`` module. The
configuration model and the startup self-check stay in the application, because their
fields are instance identity (``AGENT.md`` section 8); the plumbing here is the same for
both instances, so it lives with the skeleton (ADR-0009 decision 2).

Signal handling has to cooperate with sippy's blocking event loop: ``ED2.loop()`` cannot
be interrupted from a handler, so the handlers only request shutdown and the loop is
stopped from a timer owned by the loop (ADR-0002, and ``AGENT.md`` section 6). Nothing here
knows which application installed the handlers, and nothing here may import an application
package (REQ-F-030).
"""

from __future__ import annotations

import signal
import socket
from types import FrameType

from as_platform.errors import AsError, SkeletonErrorCode

__all__ = ["ShutdownController", "check_port_available", "install_signal_handlers"]


def check_port_available(address: str, port: int, *, family: int = socket.AF_INET) -> None:
    """Check that a UDP port can be bound before the service starts.

    The probe is deliberately generic — it takes the address and port it must test rather
    than a settings object — so any AS instance can run it against its own signalling port
    without the library knowing the configuration schema.

    Args:
        address: Local address to bind.
        port: UDP port to bind.
        family: Socket family used for the check.

    Raises:
        AsError: ``AS-CFG-003`` when the port cannot be bound.
    """
    probe = socket.socket(family, socket.SOCK_DGRAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((address, port))
    except OSError as exc:
        raise AsError(
            SkeletonErrorCode.CFG_PORT_UNAVAILABLE,
            f"cannot bind UDP {address}:{port}: {exc}",
            context={"address": address, "port": str(port)},
        ) from exc
    finally:
        probe.close()


class ShutdownController:
    """Cooperative shutdown state shared between signal handlers and the event loop."""

    def __init__(self) -> None:
        """Create a controller in the running state."""
        self._requested = False
        self._reason: str | None = None

    @property
    def requested(self) -> bool:
        """Whether a shutdown has been requested."""
        return self._requested

    @property
    def reason(self) -> str | None:
        """Why the shutdown was requested, for example ``SIGTERM``."""
        return self._reason

    def request(self, reason: str) -> None:
        """Request a graceful shutdown.

        Args:
            reason: Short description of the trigger.
        """
        self._requested = True
        self._reason = reason


def install_signal_handlers(controller: ShutdownController) -> None:
    """Install ``SIGTERM`` and ``SIGINT`` handlers that only request shutdown.

    The handlers must not stop the sippy event loop themselves; a handler runs between
    bytecodes and the loop owns the sockets.

    Args:
        controller: Shutdown state the handlers write to.
    """

    def _handler(signum: int, _frame: FrameType | None) -> None:
        controller.request(f"signal {signal.Signals(signum).name}")

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, _handler)
