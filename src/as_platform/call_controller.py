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

"""The B2BUA controller shell: the relay mechanics both AS instances share.

sippy calls ``recv_event(event, ua)`` on a controller, which relays the call control events
between the answering UA (the trunk leg, ``uaA``) and the originating UA (the next-hop leg,
``uaO``). Both applications of this repository need exactly that mechanism and differ only
in the decision they take, so the mechanism lives here and the decision is the single
application hook (ADR-0009 decision 4):

``BaseCallController.apply_call_policy(event)`` keeps the name the LLD calls "the single
seam" (``docs/architecture/lld.md`` section 11.2); it calls ``self.decide(event)``, which
returns a :class:`PolicyDecision`, and applies it. The base owns the relay, the failover
walk, the timers, the trace, the log, the disposition recording and the peer-status key; the
application owns the decision only.

**The per-application vocabulary travels as data.** :class:`PolicyDecision` carries the
trace summary and the log messages and fields of the application that produced it, so the
two applications' current lines are reproduced byte for byte without branching on "which
application am I" (REQ-F-031). Two values on the relay line are known only here — the called
number of the event being originated and the hop of the attempt being made — so the base
fills them in when the application asked for the field by name (``called_number``,
``next_hop``); every other field is passed through untouched.

**The one-leg invariant.** A call may have exactly one leg — ``uaA`` — for its whole
lifetime: a rejected call is *UAS behaviour, not B2BUA*, so it never originates a second leg
and ``uaO`` stays ``None`` from the first event to the last. Nothing here dereferences
``uaO`` without a ``None`` check, a reject needs no routing decision, the disposition is
supplied rather than derived, and :meth:`BaseCallController.dispose` guards ``None``
(``docs/architecture/lld.md`` section 9.6).

Nothing here imports an application package (REQ-F-030).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sippy.CCEvents import (
    CCEventConnect,
    CCEventDisconnect,
    CCEventFail,
    CCEventRing,
    CCEventTry,
    CCEventUpdate,
)
from sippy.SipAddress import SipAddress
from sippy.SipConf import SipConf
from sippy.SipContact import SipContact
from sippy.SipHeader import SipHeader
from sippy.SipURL import SipURL
from sippy.UA import UA

from as_platform.errors import AsError, SkeletonErrorCode
from as_platform.hop import NextHop
from as_platform.observability.logging import LogDirection, get_logger, log_event
from as_platform.observability.metrics import (
    CallDisposition,
    MetricsRegistry,
    PeerStatus,
    get_metrics_registry,
)
from as_platform.observability.tracing import TraceRecorder, get_trace_recorder
from as_platform.sip_adapter import PASSTHROUGH_HEADERS, is_allowed_peer

__all__ = [
    "LEG_NEXT_HOP",
    "LEG_TRUNK",
    "BaseCallController",
    "BaseCallMap",
    "PolicyAction",
    "PolicyDecision",
]

_LOGGER = get_logger(__name__)

#: Leg names used in the ``leg`` trace attribute: ``trunk`` is the leg towards the S-SBC,
#: ``next_hop`` the leg the AS originates (see :class:`as_platform.sip_adapter.CallLeg`).
LEG_TRUNK = "trunk"
LEG_NEXT_HOP = "next_hop"

#: How many seconds the outbound INVITE is given to see any response before the originating
#: leg is torn down and the next failover hop is tried. Tuned for the loopback POC; a real
#: deployment sets this per next hop or via configuration.
_DEFAULT_NEXT_HOP_EXPIRE = 3.0

#: Peer-status key used when a failover attempt has no serving hop to blame.
_UNKNOWN_HOP_KEY = "next_hop"


@contextmanager
def _sip_identity(global_config: dict[str, Any]) -> Iterator[None]:
    """Pin the process-wide sippy identity to this AS while a message is generated.

    ``SipConf`` is a module-level singleton and sippy reads it while it builds a ``Via`` or
    a default ``Contact``. The AS instances and the mock S-SBC are separate processes in
    production (ADR-0002) but share one interpreter in the integration and e2e tests, so an
    AS pins its own address, port and user agent name for the duration of one synchronous
    message generation and puts the previous values back afterwards.

    Args:
        global_config: sippy global configuration of the AS process.

    Yields:
        ``None``; the identity is in place for the body of the ``with`` block.
    """
    saved = (SipConf.my_address, SipConf.my_port, SipConf.my_uaname)
    SipConf.my_address = str(global_config.get("_sip_address", SipConf.my_address))
    SipConf.my_port = int(global_config.get("_sip_port", SipConf.my_port))
    SipConf.my_uaname = str(global_config.get("_sip_uaname", SipConf.my_uaname))
    try:
        yield
    finally:
        SipConf.my_address, SipConf.my_port, SipConf.my_uaname = saved


def _local_contact(global_config: dict[str, Any]) -> Any:
    """Build the Contact header the AS puts on its own messages.

    Args:
        global_config: sippy global configuration of the AS process.

    Returns:
        A ``SipContact`` pointing at the trunk address and port of the AS.
    """
    url = SipURL(
        host=str(global_config.get("_sip_address", SipConf.my_address)),
        port=int(global_config.get("_sip_port", SipConf.my_port)),
        transport=SipConf.my_transport,
    )
    return SipContact(address=SipAddress(url=url))


def _source_address(request: Any) -> str:
    """Return the source IP address of a SIP message received on the trunk.

    This is the value the peer allowlist compares against (``AGENT.md`` section 9); the
    ``peer`` log field carries the port as well, see :func:`_source_peer`.

    Args:
        request: A parsed sippy ``SipRequest``.

    Returns:
        The source address as a string, or ``"-"`` when the stack did not record one.
    """
    source = request.getSource()
    if source is None:
        return "-"
    return str(source[0])


def _source_peer(request: Any) -> str:
    """Return the source of a SIP message as ``address:port`` for log and trace fields.

    Args:
        request: A parsed sippy ``SipRequest``.

    Returns:
        The remote address and port, or ``"-"`` when the stack did not record one.
    """
    source = request.getSource()
    if source is None:
        return "-"
    return f"{source[0]}:{source[1]}"


def _describe_event(event: Any) -> tuple[str, str]:
    """Render a sippy call control event as ``(method, summary)`` for logs and traces.

    Args:
        event: A sippy ``CCEvent``.

    Returns:
        The SIP method or status code and a short lower case English summary.
    """
    if isinstance(event, CCEventTry):
        return ("INVITE", "invite received")
    if isinstance(event, (CCEventRing, CCEventConnect)):
        data = event.getData()
        if data is None:
            return ("180", "ringing")
        code, reason = str(data[0]), str(data[1])
        return (code, f"{code} {reason}".strip())
    if isinstance(event, CCEventFail):
        data = event.getData()
        if data is None:
            return ("500", "call failed")
        return (str(data[0]), f"{data[0]} {data[1]}".strip())
    if isinstance(event, CCEventDisconnect):
        return ("BYE", "call released")
    if isinstance(event, CCEventUpdate):
        return ("INVITE", "session update")
    return (type(event).__name__, "call control event")


def _with_attempt_fields(
    fields: dict[str, Any], hop: NextHop, called_number: str
) -> dict[str, Any]:
    """Fill the two relay fields only the base can know, when the application asked for them.

    The called number comes from the event that is being originated and the next hop from
    the attempt being made — the first hop, or a failover one — so neither is known when the
    application takes its decision. An application that emits neither field keeps its line
    unchanged, which is what makes this a vocabulary and not a branch on the application.

    Args:
        fields: Trace attributes or log fields supplied by the application.
        hop: The hop this attempt goes to.
        called_number: Called number of the event being originated.

    Returns:
        A copy of ``fields`` with ``called_number`` and ``next_hop`` resolved.
    """
    filled = dict(fields)
    if "called_number" in filled:
        filled["called_number"] = called_number
    if "next_hop" in filled:
        filled["next_hop"] = hop.name
    return filled


class PolicyAction(Enum):
    """What the base must do with a call once the application has decided."""

    RELAY = "relay"
    REJECT = "reject"


@dataclass
class PolicyDecision:
    """The one value the application hands the base (ADR-0009 decision 4).

    It is plain data, so the base can apply it without knowing either use case's
    vocabulary: every string the application's trace and log lines carry travels in the
    fields below rather than in a branch on "which application am I" (REQ-F-031).

    Attributes:
        action: Relay the call towards the next hops, or answer it on the trunk.
        outbound_event: Relay path: the ``CCEventTry`` to originate, already rewritten or
            the original one for a pass-through.
        next_hops: Relay path: the ordered next hops to try; the first is used now and the
            rest stay available for failover.
        error: Reject path: the ``AsError`` carrying the family code, its SIP status and its
            reason phrase.
        disposition: Outcome to count for the call, supplied rather than derived.
        attributes: Extra trace fields for this decision — the reject fields beside ``leg``
            and ``error_code``, or the relay fields of the originate event.
        reject_trace_summary: Reject path: the trace summary of the answer on the trunk leg.
        reject_log_message: Reject path: the log message of the answer on the trunk leg.
        reject_log_fields: Reject path: extra log fields beside ``error.as_log_fields()``.
        relay_log_message: Relay path: the log message of the originate event.
        relay_log_fields: Relay path: extra log fields of the originate event.
    """

    action: PolicyAction
    outbound_event: Any = None
    next_hops: list[NextHop] = field(default_factory=list)
    error: AsError | None = None
    disposition: CallDisposition = CallDisposition.REJECTED
    attributes: dict[str, Any] = field(default_factory=dict)
    reject_trace_summary: str = ""
    reject_log_message: str = ""
    reject_log_fields: dict[str, Any] = field(default_factory=dict)
    relay_log_message: str = ""
    relay_log_fields: dict[str, Any] = field(default_factory=dict)


class BaseCallController:
    """One B2BUA call: relays events between the trunk leg and the next-hop leg.

    The relay mechanics are complete here; the decision is the application hook
    :meth:`decide`, reached through :meth:`apply_call_policy`. See the module docstring for
    the seam, the vocabulary-as-data rule and the one-leg invariant.

    Attributes:
        metrics: Counter registry.
        tracer: Per-Call-ID trace recorder.
        global_config: sippy global configuration of the AS process.
        next_hop: ``(address, port)`` of the next hop the outbound INVITE is sent to.
        call_id: SIP Call-ID of the call, known once the INVITE has been terminated.
        uaA: Answering UA, the leg towards the S-SBC; always present once the INVITE is
            terminated.
        uaO: Originating UA, the leg towards the next hop; ``None`` until the call is
            routed, and ``None`` for the whole lifetime of a UAS-only (rejected) call.
    """

    def __init__(
        self,
        *,
        metrics: MetricsRegistry | None = None,
        tracer: TraceRecorder | None = None,
        global_config: dict[str, Any] | None = None,
        next_hop: tuple[str, int] | None = None,
    ) -> None:
        """Create a call controller.

        Args:
            metrics: Counter registry; the process-wide one is used when omitted.
            tracer: Trace recorder; the process-wide one is used when omitted.
            global_config: sippy global configuration; empty when the controller is
                exercised without a stack (unit tests).
            next_hop: ``(address, port)`` of the next hop for the outbound INVITE.
        """
        self.metrics = metrics or get_metrics_registry()
        self.tracer = tracer or get_trace_recorder()
        self.global_config: dict[str, Any] = dict(global_config or {})
        self.next_hop = next_hop
        self.call_id: str = "-"
        self.trunk_peer: str = "-"
        self.uaA: Any = None
        self.uaO: Any = None
        self._trunk_request: Any = None
        # The decision currently being applied. It is kept because a failover attempt
        # re-originates the same event and must reproduce the same trace and log line.
        self._policy: PolicyDecision | None = None
        # The translated ``CCEventTry``, so a failover attempt can re-originate it towards
        # a different next hop.
        self._pending_event: Any = None
        self._failover_hops: list[NextHop] = []
        self._serving_hop: NextHop | None = None
        self._no_answer_timer: Any = None

    # --- the application hook -----------------------------------------------

    def decide(self, event: Any) -> PolicyDecision:
        """Take the decision for this call — the only application hook.

        The application returns a reject :class:`PolicyDecision` for a failure it cannot
        relay and does **not** let an :class:`AsError` escape from here: the base applies
        the decision as data and cannot build the application's reject vocabulary, so the
        conversion happens in the application (the reject trace summary and log message
        travel in the :class:`PolicyDecision`). A ``RELAY`` decision always names at least
        one hop; an application with no hop to relay to rejects the call itself.

        Args:
            event: The ``CCEventTry`` raised by the answering leg.

        Returns:
            The decision the base applies.

        Raises:
            NotImplementedError: Always; every application supplies its own decision.
        """
        raise NotImplementedError

    def _inbound_fields(self, request: Any) -> dict[str, Any]:
        """Return the application's extra fields for the inbound INVITE trace and log line.

        The default is none: the base records the Call-ID and the peer of the INVITE, and an
        application that reads something else off the request (the called number, the
        calling party) adds it here. The same mapping is used for the trace attributes and
        for the log fields.

        Args:
            request: The trunk INVITE.

        Returns:
            Extra trace attributes and log fields, empty by default.
        """
        return {}

    # --- trunk side ---------------------------------------------------------

    def recv_request(self, request: Any, transaction: Any) -> Any:
        """Terminate an INVITE arriving on the trunk and create the answering leg.

        Args:
            request: The parsed SIP request.
            transaction: The sippy server transaction of the request.

        Returns:
            The sippy callback triple ``(response, cancel_cb, noack_cb)``.
        """
        self.call_id = str(request.getHFBody("call-id"))
        self.trunk_peer = _source_peer(request)
        inbound_fields = self._inbound_fields(request)
        self.metrics.record_call_started()
        self.metrics.set_peer_status(f"{self.trunk_peer}:trunk", PeerStatus.REACHABLE)
        self._record(
            LogDirection.INBOUND,
            LEG_TRUNK,
            "INVITE",
            "invite received from the trunk",
            peer=self.trunk_peer,
            attributes=inbound_fields,
        )
        log_event(
            _LOGGER,
            logging.INFO,
            "invite received on the trunk",
            call_id=self.call_id,
            direction=LogDirection.INBOUND,
            peer=self.trunk_peer,
            method="INVITE",
            **inbound_fields,
        )
        self.uaA = UA(self.global_config, self.recv_event)
        self.uaA.local_ua = str(self.global_config.get("_sip_uaname", ""))
        self.uaA.lContact = _local_contact(self.global_config)
        with _sip_identity(self.global_config):
            return self.uaA.recvRequest(request, transaction)

    def capture_trunk_request(self, request: Any) -> None:
        """Remember the trunk INVITE so its headers can be copied to the outbound leg.

        Args:
            request: The inbound INVITE.
        """
        self._trunk_request = request

    # --- sippy event hook ---------------------------------------------------

    def recv_event(self, event: Any, ua: Any) -> None:
        """Relay one call control event between the two legs.

        A ``CCEvent`` raised by a previous originating leg that has already been replaced by
        a failover attempt is ignored: its expiry timer fires after the controller has moved
        on, and relaying it would tear the new leg down.

        Args:
            event: A sippy ``CCEvent``.
            ua: The sippy UA the event came from.
        """
        if ua is self.uaA:
            self._relay_from_trunk(event)
            return
        if ua is not self.uaO:
            return  # stale event from a UA that has been replaced by failover
        self._relay_from_next_hop(event)

    def _relay_from_trunk(self, event: Any) -> None:
        """Handle an event raised by the answering (trunk) leg.

        ``uaO`` is ``None`` until the call is routed; for a rejected call it stays ``None``
        and this method is the whole lifetime of the call.

        Args:
            event: A sippy ``CCEvent``.
        """
        method, summary = _describe_event(event)
        if self.uaO is None:
            if not isinstance(event, CCEventTry):
                # Nothing has been originated yet and the caller already gave up: there is
                # nothing to relay, so the call is torn down on the trunk leg only.
                self.uaA.recvEvent(CCEventDisconnect())
                return
            self._originate_outbound_leg(event)
            return
        self._record(
            LogDirection.INBOUND,
            LEG_TRUNK,
            method,
            f"{summary} on the trunk leg",
            peer=self.trunk_peer,
        )
        self.uaO.recvEvent(event)

    def _relay_from_next_hop(self, event: Any) -> None:
        """Handle an event raised by the originating (next-hop) leg.

        On a failure from the serving next hop, the next failover hop is tried before the
        failure is relayed to the trunk leg (``AS-PEER-002`` / ``503`` semantics). When no
        failover hop remains, the failure is relayed so the caller sees it. A single-hop
        application is the same code with a one-element hop list: nothing is left to try, so
        the failure goes straight to the trunk leg.

        A ``CCEventFail`` or a pre-connect ``CCEventDisconnect`` from the originating leg
        (the no-answer timer calls ``disconnect()``) is treated as a failover trigger when a
        hop remains.

        Args:
            event: A sippy ``CCEvent``.
        """
        method, summary = _describe_event(event)
        if isinstance(event, (CCEventRing, CCEventConnect)):
            self._cancel_no_answer_timer()
        connected = self.uaA is not None and bool(self.uaA.isConnected())
        if (
            isinstance(event, (CCEventFail, CCEventDisconnect))
            and not connected
            and self._try_failover()
        ):
            self._cancel_no_answer_timer()
            self._record(
                LogDirection.INBOUND,
                LEG_NEXT_HOP,
                method,
                f"{summary} on the next-hop leg; trying failover",
                peer=self._next_hop_peer(),
                attributes={
                    "failed_hop": self._serving_hop.name if self._serving_hop else "",
                },
            )
            return
        self._record(
            LogDirection.INBOUND,
            LEG_NEXT_HOP,
            method,
            f"{summary} on the next-hop leg",
            peer=self._next_hop_peer(),
        )
        if isinstance(event, (CCEventFail, CCEventDisconnect)):
            self._cancel_no_answer_timer()
            self._record_disposition(event)
        if self.uaA is not None:
            self.uaA.recvEvent(event)
        if isinstance(event, (CCEventRing, CCEventConnect)) and method != "ACK":
            # The relay above makes the answering leg send the response on the trunk.
            self._record(
                LogDirection.OUTBOUND,
                LEG_TRUNK,
                method,
                f"{summary} relayed to the trunk leg",
                peer=self.trunk_peer,
            )

    def _try_failover(self) -> bool:
        """Try the next failover hop when the serving hop failed.

        Returns:
            ``True`` when a failover attempt was started, ``False`` when no hop remains and
            the caller must be told the call failed.
        """
        if not self._failover_hops:
            return False
        failed = self._serving_hop
        self.metrics.set_peer_status(
            self._peer_status_key(failed) if failed else _UNKNOWN_HOP_KEY,
            PeerStatus.UNREACHABLE,
        )
        next_hop = self._failover_hops.pop(0)
        self.uaO = None
        log_event(
            _LOGGER,
            logging.WARNING,
            "next hop failed; trying failover hop",
            call_id=self.call_id,
            direction=LogDirection.INTERNAL,
            failed_hop=failed.name if failed else "",
            failover_hop=next_hop.name,
            error_code=SkeletonErrorCode.PEER_UNREACHABLE.code,
        )
        self._originate_towards(next_hop)
        return True

    def _originate_outbound_leg(self, event: Any) -> None:
        """Apply the decision and originate the outbound INVITE.

        The seam (:meth:`apply_call_policy`) decides the fate of the call: relay, or reject
        on the trunk. On a relay, the first next hop of the decision is used; the remaining
        hops stay available for failover (:meth:`_relay_from_next_hop`). On a rejection, the
        trunk leg is answered with the SIP status the decision carries and no outbound leg
        is created.

        Args:
            event: The ``CCEventTry`` raised by the answering leg.
        """
        decision = self.apply_call_policy(event)
        if decision.action is not PolicyAction.RELAY:
            self._reject_on_trunk(decision)
            return
        self._pending_event = decision.outbound_event
        self._failover_hops = list(decision.next_hops)
        # A relay decision always names the hop to try first; an application with no hop to
        # relay to rejects the call itself (see the two applications' ``decide``).
        self._originate_towards(self._failover_hops.pop(0))

    def _originate_towards(self, hop: NextHop) -> None:
        """Originate the outbound INVITE towards one next hop.

        A controller-owned timer (``Timeout``) watches for a no-answer condition: when it
        fires before the leg connected, the controller injects a ``CCEventFail`` on the
        originating leg, which sippy turns into a transaction failure and which the failover
        path in :meth:`_relay_from_next_hop` turns into an attempt at the next hop. This is
        deliberately not sippy's ``expire_time``: that timer is anchored to the INVITE event
        ``rtime`` and would fire immediately on a failover attempt whose pending event
        carries the original timestamp.

        The serving hop is stored as a :class:`NextHop` — the peer-status key and the
        failover walk need the value object — while the originating ``UA`` still receives
        the ``(address, port)`` tuple at the sippy boundary.

        Args:
            hop: Next hop to send the INVITE to.
        """
        from sippy.Time.Timeout import Timeout

        self._serving_hop = hop
        self.next_hop = (hop.address, hop.port)
        self.uaO = UA(self.global_config, event_cb=self.recv_event, nh_address=self.next_hop)
        self.uaO.local_ua = str(self.global_config.get("_sip_uaname", ""))
        self.uaO.lContact = _local_contact(self.global_config)
        expire_seconds = float(
            self.global_config.get("_next_hop_expire_seconds", _DEFAULT_NEXT_HOP_EXPIRE)
        )
        self._no_answer_timer = Timeout(self._on_next_hop_no_answer, expire_seconds, 1)
        called_number = str(self._pending_event.getData()[2])
        message, trace_attributes, log_fields = self._relay_vocabulary(hop, called_number)
        self.metrics.set_peer_status(self._peer_status_key(hop), PeerStatus.REACHABLE)
        self._record(
            LogDirection.OUTBOUND,
            LEG_NEXT_HOP,
            "INVITE",
            message,
            peer=self._next_hop_peer(),
            attributes=trace_attributes,
        )
        log_event(
            _LOGGER,
            logging.INFO,
            message,
            call_id=self.call_id,
            direction=LogDirection.OUTBOUND,
            peer=self._next_hop_peer(),
            method="INVITE",
            **log_fields,
        )
        with _sip_identity(self.global_config):
            self.uaO.recvEvent(self._pending_event)

    def _relay_vocabulary(
        self, hop: NextHop, called_number: str
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Return the relay log message and the per-attempt trace and log fields.

        Args:
            hop: The hop this attempt goes to.
            called_number: Called number of the event being originated.

        Returns:
            The application's originate log message, its trace attributes and its log
            fields, with ``called_number`` and ``next_hop`` resolved for this attempt.
        """
        policy = self._policy
        if policy is None:
            return ("", {}, {})
        return (
            policy.relay_log_message,
            _with_attempt_fields(policy.attributes, hop, called_number),
            _with_attempt_fields(policy.relay_log_fields, hop, called_number),
        )

    def _on_next_hop_no_answer(self) -> None:
        """Tear the serving next hop down when it did not answer in time.

        The controller-owned no-answer timer fires before any response arrived from the
        serving hop. Injecting a ``CCEventFail`` makes sippy cancel the outstanding
        transaction and raise the fail event on the originating leg, which the failover path
        turns into an attempt at the next hop.
        """
        if self.uaO is None:
            return
        connected = self.uaA is not None and bool(self.uaA.isConnected())
        if connected:
            return
        log_event(
            _LOGGER,
            logging.WARNING,
            "next hop did not answer in time",
            call_id=self.call_id,
            direction=LogDirection.INTERNAL,
            next_hop=self._no_answer_hop_label(),
            error_code=SkeletonErrorCode.PEER_UNREACHABLE.code,
        )
        # ``disconnect()`` enqueues a ``CCEventDisconnect`` and drives the state transition
        # so sippy emits the event to the event callback, which the failover path in
        # :meth:`_relay_from_next_hop` turns into an attempt at the next hop.
        self.uaO.disconnect()

    def _cancel_no_answer_timer(self) -> None:
        """Cancel the no-answer timer when the serving hop responded."""
        if self._no_answer_timer is not None:
            self._no_answer_timer.cancel()
            self._no_answer_timer = None

    def dispose(self) -> None:
        """Drop the controller-owned timers so they cannot outlive the stack.

        The no-answer timer watches a next hop for :data:`_DEFAULT_NEXT_HOP_EXPIRE` seconds.
        A call that is still waiting when the process stops would otherwise have its timer
        fire into a transaction manager that has already been shut down, whose
        ``global_config['_sip_tm']`` is ``None``. Called from :meth:`BaseCallMap.dispose`
        when the stack stops (P8a).
        """
        self._cancel_no_answer_timer()

    # --- the translation seam -----------------------------------------------

    def apply_call_policy(self, event: Any) -> PolicyDecision:
        """Apply the application's policy to an inbound INVITE before it leaves the AS.

        This is the **single seam** where an inbound INVITE is decided on before it leaves
        the AS (``AGENT.md`` section 1, ``docs/architecture/lld.md`` section 3.3): it asks
        :meth:`decide` for the decision and remembers it, because a failover attempt
        re-originates the same event and must reproduce the same trace and log line.

        Args:
            event: The ``CCEventTry`` raised by the answering leg.

        Returns:
            The decision the base is about to apply.
        """
        decision = self.decide(event)
        self._policy = decision
        return decision

    def _reject_on_trunk(self, decision: PolicyDecision) -> None:
        """Answer the trunk leg with the SIP status the application's decision carries.

        A reject needs a SIP status, its reason phrase and an :class:`AsError`; it does
        **not** need a routing decision. Removing that requirement is the point of the
        one-leg relaxation: an AS that decides on the *calling* party rejects from a verdict,
        with no routing decision anywhere (ADR-0007, ``docs/architecture/lld.md``
        section 9.6).

        This is also where the one-leg invariant is enforced. A reject is exactly the
        UAS-only case, so the answer goes out on ``uaA`` and ``uaO`` — which is ``None`` —
        is never touched.

        The error code is **not** counted here: the application counts it where it knows it
        has one (the number-translation AS in its routing decision, the anti-fraud AS in its
        verdict), so a single failure is never counted twice.

        Args:
            decision: The reject decision, carrying the error, the disposition and the
                application's trace summary and log vocabulary.
        """
        error = decision.error
        assert error is not None  # a reject decision always carries its error
        self._cancel_no_answer_timer()
        self.metrics.record_call_disposition(decision.disposition)
        attributes: dict[str, Any] = {"leg": LEG_TRUNK, "error_code": error.code.code}
        attributes.update(decision.attributes)
        self.tracer.record(
            self.call_id,
            LogDirection.OUTBOUND,
            str(error.sip_status),
            decision.reject_trace_summary,
            peer=self.trunk_peer,
            rule_id=decision.attributes.get("rule_id") or None,
            attributes=attributes,
        )
        log_event(
            _LOGGER,
            logging.WARNING,
            decision.reject_log_message,
            call_id=self.call_id,
            direction=LogDirection.OUTBOUND,
            peer=self.trunk_peer,
            method=str(error.sip_status),
            **decision.reject_log_fields,
            **error.as_log_fields(),
        )
        if self.uaA is not None:
            self.uaA.recvEvent(CCEventFail((error.sip_status, error.sip_phrase, None)))

    def _pass_through_headers(self, request: Any) -> tuple[Any, ...]:
        """Copy the pass-through headers of the trunk INVITE for the outbound INVITE.

        Args:
            request: The inbound INVITE, or ``None`` when it is not available.

        Returns:
            Copies of the headers listed in
            :data:`as_platform.sip_adapter.PASSTHROUGH_HEADERS`, in wire order.
        """
        if request is None:
            return ()
        headers: list[Any] = []
        for name in PASSTHROUGH_HEADERS:
            for body in request.getHFBodys(name):
                headers.append(SipHeader(name=name, body=body.getCopy()))
        return tuple(headers)

    # --- counters and traces ------------------------------------------------

    def _record_disposition(self, event: Any) -> None:
        """Count the final outcome of the call.

        Args:
            event: The ``CCEventFail`` or ``CCEventDisconnect`` that ended the call.
        """
        if isinstance(event, CCEventFail):
            disposition = CallDisposition.FAILED
        elif self.uaA is not None and bool(self.uaA.isConnected()):
            disposition = CallDisposition.COMPLETED
        else:
            disposition = CallDisposition.ABANDONED
        self.metrics.record_call_disposition(disposition)
        log_event(
            _LOGGER,
            logging.INFO,
            "call finished",
            call_id=self.call_id,
            direction=LogDirection.INTERNAL,
            disposition=disposition.value,
        )

    def _record(
        self,
        direction: str,
        leg: str,
        method: str,
        summary: str,
        *,
        peer: str = "-",
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Write one event into the Call-ID keyed trace.

        Args:
            direction: One of :class:`LogDirection` values.
            leg: ``trunk`` or ``next_hop``.
            method: SIP method or status code.
            summary: Short human readable description.
            peer: Remote address of the message.
            attributes: Additional structured details.
        """
        merged = {"leg": leg}
        merged.update(attributes or {})
        self.tracer.record(self.call_id, direction, method, summary, peer=peer, attributes=merged)
        log_event(
            _LOGGER,
            logging.DEBUG,
            summary,
            call_id=self.call_id,
            direction=direction,
            peer=peer,
            method=method,
            leg=leg,
        )

    def _next_hop_peer(self) -> str:
        """Render the next hop as ``address:port`` for log and trace fields.

        Returns:
            The next hop address, or ``"-"`` when none is configured.
        """
        if self.next_hop is None:
            return "-"
        return f"{self.next_hop[0]}:{self.next_hop[1]}"

    def _peer_status_key(self, hop: NextHop) -> str:
        """Render the key a hop is tracked under in the peer-status counters.

        The default is the configured hop name followed by its address and port. An
        application whose single hop has no name of its own in its configuration overrides
        this — the anti-fraud AS renders ``address:port`` — so its key stays exactly what it
        was before the shell moved here.

        Args:
            hop: The hop whose status is being recorded.

        Returns:
            The peer-status key of the hop.
        """
        return f"{hop.name}:{hop.address}:{hop.port}"

    def _no_answer_hop_label(self) -> str:
        """Render the hop a no-answer warning names.

        The default is the serving hop's name. An application whose hop name is not part of
        its own configuration overrides this, so its warning keeps the label it had before.

        Returns:
            The label of the serving hop, or ``""`` when no hop is being served.
        """
        return self._serving_hop.name if self._serving_hop else ""


class BaseCallMap:
    """Process-wide entry point for requests arriving on the SIP trunk.

    The trunk is untrusted: a request from an address outside the configured allowlist is
    answered with ``403`` and ``AS-PEER-001`` before any call state is created
    (``AGENT.md`` section 9). Everything else is handed to a fresh controller, which owns the
    legs of that one call; the application supplies the controller through
    :meth:`_build_controller` (``docs/architecture/lld.md`` section 9.2).

    Attributes:
        global_config: sippy global configuration of the AS process.
        allowed_peers: Source addresses accepted on the trunk.
        metrics: Counter registry.
        tracer: Trace recorder.
        controllers: The calls currently known to the AS, in creation order.
    """

    def __init__(
        self,
        global_config: dict[str, Any],
        *,
        allowed_peers: tuple[str, ...] = (),
        metrics: MetricsRegistry | None = None,
        tracer: TraceRecorder | None = None,
    ) -> None:
        """Create the trunk call map.

        Args:
            global_config: sippy global configuration; ``nh_addr`` carries the next hop.
            allowed_peers: Source addresses accepted on the trunk.
            metrics: Counter registry; the process-wide one is used when omitted.
            tracer: Trace recorder; the process-wide one is used when omitted.
        """
        self.global_config = global_config
        self.allowed_peers = allowed_peers
        self.metrics = metrics or get_metrics_registry()
        self.tracer = tracer or get_trace_recorder()
        self.controllers: list[BaseCallController] = []

    def recv_request(self, request: Any, transaction: Any) -> Any:
        """Handle a request arriving on the trunk.

        Args:
            request: The parsed SIP request.
            transaction: The sippy server transaction of the request.

        Returns:
            The sippy callback triple ``(response, cancel_cb, noack_cb)``.
        """
        source = _source_address(request)
        if not is_allowed_peer(source, list(self.allowed_peers)):
            return self._reject_peer(request, source, _source_peer(request))
        if request.getHFBody("to").getTag() is not None:
            return (request.genResponse(481, "Call Leg/Transaction Does Not Exist"), None, None)
        if request.getMethod() != "INVITE":
            return (request.genResponse(501, "Not Implemented"), None, None)
        controller = self._new_controller()
        controller.capture_trunk_request(request)
        return controller.recv_request(request, transaction)

    def _new_controller(self) -> BaseCallController:
        """Create and remember the controller of one call.

        Returns:
            A controller wired to the trunk call map's stack and next hop.
        """
        controller = self._build_controller(self._configured_next_hop())
        self.controllers.append(controller)
        return controller

    def _build_controller(self, next_hop: tuple[str, int] | None) -> BaseCallController:
        """Build the application's controller — the one process-shell hook.

        Args:
            next_hop: ``(address, port)`` of the configured next hop, or ``None``.

        Returns:
            The controller of one call.

        Raises:
            NotImplementedError: Always; every application supplies its own controller.
        """
        raise NotImplementedError

    def _configured_next_hop(self) -> tuple[str, int] | None:
        """Return the next hop configured for this process.

        Returns:
            The ``(address, port)`` pair of ``nh_addr``, or ``None`` when it is not set.
        """
        configured_hop = self.global_config.get("nh_addr")
        if configured_hop is None:
            return None
        return (str(configured_hop[0]), int(configured_hop[1]))

    def dispose(self) -> None:
        """Cancel the timers of every call this map still knows about.

        Called when the signalling stack stops: the controllers of calls that are still
        waiting for a next hop own loop timers that would otherwise fire into a transaction
        manager which has already been shut down. See
        :meth:`BaseCallController.dispose` and ``AsStack.stop`` (P8a).
        """
        for controller in self.controllers:
            controller.dispose()

    def _reject_peer(self, request: Any, source: str, peer: str) -> Any:
        """Answer a request from an address that is not an allowed trunk peer.

        Args:
            request: The parsed SIP request.
            source: Source address of the request, as checked against the allowlist.
            peer: Source address and port, for the ``peer`` log and trace field.

        Returns:
            The sippy callback triple carrying the ``403`` response.
        """
        error = AsError(
            SkeletonErrorCode.PEER_NOT_ALLOWED,
            f"source address {source} is not an allowed trunk peer",
            context={"source": source, "sip_method": str(request.getMethod())},
        )
        call_id = str(request.getHFBody("call-id"))
        self.metrics.record_error(SkeletonErrorCode.PEER_NOT_ALLOWED.code)
        self.tracer.record(
            call_id,
            LogDirection.INBOUND,
            str(request.getMethod()),
            "403 forbidden: source is not an allowed trunk peer",
            peer=peer,
            attributes={"leg": LEG_TRUNK},
        )
        log_event(
            _LOGGER,
            logging.WARNING,
            "request rejected: source address is not an allowed trunk peer",
            call_id=call_id,
            direction=LogDirection.INBOUND,
            peer=peer,
            method=str(request.getMethod()),
            **error.as_log_fields(),
        )
        return (request.genResponse(403, "Forbidden"), None, None)
