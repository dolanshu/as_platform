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

"""The AS stack shell: the sippy process skeleton both AS instances share.

Every AS process is ``SipConf`` + ``SipTransactionManager`` + ``ED2.loop()``
(``AGENT.md`` section 5). The two applications duplicate that skeleton almost verbatim,
so it lives here and the application supplies only what is instance identity: the user
agent name, the SIP logger name, the trunk call map, the internal API server and the
loop-owned reload callback (ADR-0009 decision 2, ``docs/architecture/lld.md`` section
11.2).

Two sippy facts shape this module (see ``docs/operations/troubleshooting.md``):

- ``ED2.loop()`` blocks and must run on the main thread, so shutdown is a loop-owned
  timer that only *observes* the flag the signal handlers set.
- ``global_config['_sip_logger']`` must be a ``SipLogger``; ``None`` raises on the first
  inbound message.

The base takes **resolved** configuration values, never a settings object's field names,
so it never learns which application it is running. The one settings field it reads —
``log_payloads`` — is typed through a small protocol. Nothing here imports an application
package (REQ-F-030).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Generic, Protocol, TypeVar

from sippy.Core.EventDispatcher import ED2
from sippy.SipConf import SipConf
from sippy.SipLogger import SipLogger
from sippy.SipTransactionManager import SipTransactionManager
from sippy.Time.Timeout import Timeout

from as_platform.bootstrap import ShutdownController
from as_platform.call_controller import BaseCallMap
from as_platform.internal_api import InternalApiServer
from as_platform.observability.logging import LogDirection, get_logger, log_event
from as_platform.observability.metrics import MetricsRegistry, get_metrics_registry
from as_platform.observability.tracing import TraceRecorder, get_trace_recorder
from as_platform.sip_adapter import cancel_transaction_timers

__all__ = ["BaseAsStack"]

_LOGGER = get_logger(__name__)


class _SettingsLike(Protocol):
    """The one settings field the base reads from the application's configuration."""

    #: Whether sippy's SIP message log carries payloads (``AGENT.md`` section 9).
    log_payloads: bool


SettingsT = TypeVar("SettingsT", bound=_SettingsLike)


class BaseAsStack(Generic[SettingsT]):
    """The sippy signalling stack shared by every AS process.

    One instance owns the transaction manager, the trunk call map and the internal API
    server. It can run the blocking sippy loop (:meth:`run`) or be driven step by step,
    which is how the integration and e2e tests use it.

    The application subclasses this class and supplies its own instance identity through
    the class variables and the hooks below; the base owns the sippy wiring, the timers
    and the stop ordering.

    Attributes:
        settings: The loaded configuration.
        metrics: Counter registry.
        tracer: Per-Call-ID trace recorder.
        internal_api: Health and counters endpoint; ``None`` when it is not started.
        transaction_manager: The sippy transaction manager of the process.
        global_config: The sippy global configuration handed to every sippy object.
        call_map: Trunk entry point: peer allowlist and one controller per call.
    """

    #: User agent name reported on the trunk (RFC 3261 section 20.35 ``Server`` header).
    sip_user_agent_name: ClassVar[str]

    #: Name handed to sippy's :class:`SipLogger`.
    sip_logger_name: ClassVar[str]

    #: Log message emitted when the stack has bound its socket and transaction manager.
    bound_log_message: ClassVar[str] = "signalling stack bound"

    #: How often the loop-owned shutdown poller looks at the shutdown flag, in seconds.
    shutdown_poll_seconds: ClassVar[float] = 0.1

    #: How often the loop-owned reload poller checks the application's data file, in
    #: seconds. The reload is pull-based because the sippy thread must not be blocked by
    #: file I/O (ADR-0004).
    reload_poll_seconds: ClassVar[float] = 1.0

    def __init__(
        self,
        settings: SettingsT,
        *,
        sip_address: str,
        sip_port: int,
        peer_address: str,
        peer_port: int,
        allowed_peers: tuple[str, ...],
        api_address: str,
        api_port: int,
        metrics: MetricsRegistry | None = None,
        tracer: TraceRecorder | None = None,
        sip_logger: Any | None = None,
    ) -> None:
        """Create the signalling stack without binding anything yet.

        Args:
            settings: The loaded configuration.
            sip_address: Local address the trunk socket binds.
            sip_port: Local UDP port the trunk socket binds.
            peer_address: Next hop the AS originates towards.
            peer_port: Next hop UDP port.
            allowed_peers: Source addresses accepted on the trunk.
            api_address: Local address the internal API binds.
            api_port: Local TCP port the internal API binds.
            metrics: Counter registry; the process-wide one is used when omitted.
            tracer: Trace recorder; the process-wide one is used when omitted.
            sip_logger: Explicit sippy SIP message logger, used by the tooling.
        """
        self.settings = settings
        self.sip_address = sip_address
        self.sip_port = sip_port
        self.peer_address = peer_address
        self.peer_port = peer_port
        self.allowed_peers = allowed_peers
        self.api_address = api_address
        self.api_port = api_port
        self.metrics = metrics or get_metrics_registry()
        self.tracer = tracer or get_trace_recorder()
        self.internal_api: InternalApiServer | None = None
        self._sip_logger = self._build_sip_logger(sip_logger)
        self._shutdown_timer: Any = None
        self._reload_timer: Any = None
        self.transaction_manager: Any = None
        self.global_config: dict[str, Any] = {}
        self.call_map: BaseCallMap | None = None

    # --- application hooks --------------------------------------------------

    def _create_call_map(self, global_config: dict[str, Any]) -> BaseCallMap:
        """Create the application's trunk call map.

        Args:
            global_config: sippy global configuration; ``nh_addr`` carries the next hop.

        Returns:
            The trunk call map of the application.

        Raises:
            NotImplementedError: Always; every application supplies its own map.
        """
        raise NotImplementedError

    def _create_internal_api_server(self, address: str, port: int) -> InternalApiServer:
        """Create the application's internal API server.

        The application supplies its own version and its own store here, so the base needs
        no version plumbing.

        Args:
            address: Local address the server binds.
            port: Local TCP port the server binds.

        Returns:
            The internal API server of the application.

        Raises:
            NotImplementedError: Always; every application supplies its own server.
        """
        raise NotImplementedError

    def _poll_reload(self) -> None:
        """Reload the application's data file when it changed on disk.

        Runs inside the loop-owned reload timer.

        Raises:
            NotImplementedError: Always; every application supplies its own reload.
        """
        raise NotImplementedError

    def _reload_log_fields(self) -> dict[str, str]:
        """Return the reload-source log fields for the event loop start line.

        Returns:
            The field(s) naming the reloaded file — ``rules_file`` or ``screening_file``.

        Raises:
            NotImplementedError: Always; every application supplies its own field(s).
        """
        raise NotImplementedError

    # --- the sippy process shell --------------------------------------------

    def _build_sip_logger(self, sip_logger: Any | None) -> Any:
        """Build the sippy SIP message logger for the process.

        The structured application log never carries payloads. This is the separate SIP
        message channel, and it is switched by ``LOG_PAYLOADS`` (``AGENT.md`` section 9):
        when payload logging is off, sippy's message log is suppressed instead of printed.

        Args:
            sip_logger: An explicit logger, used by the capture tooling; ``None`` builds
                the default one.

        Returns:
            An object with the ``write()`` interface sippy expects.
        """
        if sip_logger is not None:
            return sip_logger
        logger = SipLogger(self.sip_logger_name)
        if not self.settings.log_payloads:
            logger.write = logger.donoting
        return logger

    def start(self) -> None:
        """Bind the trunk socket, the transaction manager and the call map.

        Raises:
            AsError: Propagated from sippy when the signalling port cannot be bound.
        """
        SipConf.my_uaname = self.sip_user_agent_name
        SipConf.my_address = self.sip_address
        SipConf.my_port = self.sip_port
        self.global_config = {
            "nh_addr": (self.peer_address, self.peer_port),
            "_sip_address": self.sip_address,
            "_sip_port": self.sip_port,
            "_sip_uaname": self.sip_user_agent_name,
            "_sip_logger": self._sip_logger,
        }
        self.call_map = self._create_call_map(self.global_config)
        self.transaction_manager = SipTransactionManager(
            self.global_config, self.call_map.recv_request
        )
        self.global_config["_sip_tm"] = self.transaction_manager
        log_event(
            _LOGGER,
            logging.INFO,
            self.bound_log_message,
            direction=LogDirection.INTERNAL,
            listen=f"{self.sip_address}:{self.sip_port}",
            next_hop=f"{self.peer_address}:{self.peer_port}",
            allowed_peers=",".join(self.allowed_peers),
        )

    def start_internal_api(self) -> InternalApiServer:
        """Start the health and counters endpoint on its own thread.

        Returns:
            The running internal API server.
        """
        server = self._create_internal_api_server(self.api_address, self.api_port)
        server.start()
        self.internal_api = server
        log_event(
            _LOGGER,
            logging.INFO,
            "internal api listening",
            direction=LogDirection.INTERNAL,
            address=f"{self.api_address}:{self.api_port}",
        )
        return server

    def run(self, shutdown: ShutdownController) -> None:
        """Run the blocking sippy event loop until a shutdown is requested.

        Signal handlers only set the flag; the timer below is owned by the loop, which is
        the only place allowed to stop it (ADR-0002, ``AGENT.md`` section 6). A second
        loop-owned timer polls the application's data file for hot reload (ADR-0004): the
        reload is pull-based because the sippy thread must not be blocked by file I/O.

        Args:
            shutdown: Shutdown state written by the signal handlers.
        """
        self._shutdown_timer = Timeout(
            self._poll_shutdown, self.shutdown_poll_seconds, -1, shutdown
        )
        self._reload_timer = Timeout(self._poll_reload, self.reload_poll_seconds, -1)
        log_event(
            _LOGGER,
            logging.INFO,
            "sippy event loop running",
            direction=LogDirection.INTERNAL,
            **self._reload_log_fields(),
            reload_poll_seconds=str(self.reload_poll_seconds),
        )
        ED2.loop()

    def stop(self) -> None:
        """Release the trunk socket, the loop timers and the internal API port.

        Everything the stack armed has to be cancelled before sippy's own
        :meth:`SipTransactionManager.shutdown` runs: that call only cancels its own
        cache-purge timer and releases the sockets, so the loop-owned timers of calls that
        are still in flight would survive it and fire into a torn-down stack. See
        :func:`as_platform.sip_adapter.cancel_transaction_timers` and the gap row "Closing a
        transaction manager mid-retransmission" in ``docs/production-gaps.md``.
        """
        if self._shutdown_timer is not None:
            self._shutdown_timer.cancel()
            self._shutdown_timer = None
        if self._reload_timer is not None:
            self._reload_timer.cancel()
            self._reload_timer = None
        if self.call_map is not None:
            self.call_map.dispose()
        if self.transaction_manager is not None:
            cancel_transaction_timers(self.transaction_manager)
            self.transaction_manager.shutdown()
            self.transaction_manager = None
        if self.internal_api is not None:
            self.internal_api.stop()
            self.internal_api = None

    @staticmethod
    def _poll_shutdown(shutdown: ShutdownController) -> None:
        """Stop the sippy loop when a shutdown has been requested.

        Args:
            shutdown: Shutdown state written by the signal handlers.
        """
        if shutdown.requested:
            ED2.breakLoop()
