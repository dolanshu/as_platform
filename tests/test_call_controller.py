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

"""Tests for the B2BUA controller shell through a minimal in-library fake subclass.

The shell is the library's most important contract (ADR-0009 decision 4): an
application supplies only ``decide()`` and the base owns the relay, the failover walk,
the timers and the trace. These tests exercise that seam without a signalling stack.
"""

from __future__ import annotations

from typing import Any

import pytest

from as_platform.call_controller import (
    BaseCallController,
    BaseCallMap,
    PolicyAction,
    PolicyDecision,
)
from as_platform.hop import NextHop
from as_platform.observability.metrics import CallDisposition


class _FakeController(BaseCallController):
    """A minimal application: it decides, and the base applies the decision."""

    def __init__(self, decision: PolicyDecision, **kwargs: Any) -> None:
        """Store the decision the fake will return."""
        super().__init__(**kwargs)
        self.decision = decision
        self.decide_calls = 0

    def decide(self, event: Any) -> PolicyDecision:
        """Return the stored decision and count the call."""
        self.decide_calls += 1
        return self.decision


class _RecordingController(_FakeController):
    """A fake controller that records when the base disposes it."""

    def __init__(self, decision: PolicyDecision, disposed: list[str]) -> None:
        """Remember the shared disposal log."""
        super().__init__(decision)
        self._disposed = disposed

    def dispose(self) -> None:
        """Record the disposal."""
        self._disposed.append("disposed")


class _FakeRequest:
    """The slice of a sippy ``SipRequest`` the trunk peer guard reads."""

    def __init__(self, source: tuple[str, int], method: str = "INVITE") -> None:
        """Remember the source address and the SIP method."""
        self._source = source
        self._method = method

    def getSource(self) -> tuple[str, int]:  # noqa: N802
        """Return the recorded source address and port."""
        return self._source

    def getMethod(self) -> str:  # noqa: N802
        """Return the recorded SIP method."""
        return self._method

    def getHFBody(self, name: str) -> str:  # noqa: N802
        """Return a header body; only the Call-ID is read by the guard."""
        return "c1"

    def genResponse(self, code: int, phrase: str) -> tuple[int, str]:  # noqa: N802
        """Return the response the guard would put on the wire."""
        return (code, phrase)


def test_decide_is_the_unimplemented_application_hook() -> None:
    """The base carries no decision; a subclass must supply one."""
    controller = BaseCallController()
    with pytest.raises(NotImplementedError):
        controller.decide(object())


def test_apply_call_policy_reaches_the_subclass_decide() -> None:
    """The single seam calls ``decide`` and returns its decision."""
    decision = PolicyDecision(action=PolicyAction.REJECT)
    controller = _FakeController(decision)
    assert controller.apply_call_policy(object()) is decision
    assert controller.decide_calls == 1


def test_policy_decision_defaults_to_a_reject_with_independent_collections() -> None:
    """A decision defaults to a reject and never shares its mutable fields."""
    first = PolicyDecision(action=PolicyAction.REJECT)
    second = PolicyDecision(action=PolicyAction.REJECT)
    assert first.disposition is CallDisposition.REJECTED
    assert first.next_hops == []
    assert first.attributes == {}
    assert first.next_hops is not second.next_hops


def test_relay_vocabulary_fills_the_two_attempt_fields_the_base_owns() -> None:
    """``called_number`` and ``next_hop`` are resolved by the base for the attempt."""
    decision = PolicyDecision(
        action=PolicyAction.RELAY,
        attributes={"called_number": "", "next_hop": "", "rule_id": "R-1"},
        relay_log_message="originating outbound invite",
        relay_log_fields={"next_hop": ""},
    )
    controller = _FakeController(decision)
    controller.apply_call_policy(object())
    hop = NextHop(name="core", address="127.0.0.1", port=15061)
    message, attributes, log_fields = controller._relay_vocabulary(hop, "+8613800138000")
    assert message == "originating outbound invite"
    assert attributes == {"called_number": "+8613800138000", "next_hop": "core", "rule_id": "R-1"}
    assert log_fields == {"next_hop": "core"}


def test_inbound_fields_default_to_none() -> None:
    """An application that reads nothing off the request adds no fields."""
    controller = _FakeController(PolicyDecision(action=PolicyAction.REJECT))
    assert controller._inbound_fields(object()) == {}


def test_peer_status_key_default_names_the_hop() -> None:
    """The default key is ``name:address:port``; an application may override it."""
    controller = _FakeController(PolicyDecision(action=PolicyAction.REJECT))
    hop = NextHop(name="core", address="127.0.0.1", port=15061)
    assert controller._peer_status_key(hop) == "core:127.0.0.1:15061"


def test_no_answer_hop_label_is_empty_without_a_serving_hop() -> None:
    """No hop is being served yet, so the warning label is empty."""
    controller = _FakeController(PolicyDecision(action=PolicyAction.REJECT))
    assert controller._no_answer_hop_label() == ""


def test_dispose_is_safe_without_a_timer() -> None:
    """A controller that armed no timer disposes cleanly."""
    _FakeController(PolicyDecision(action=PolicyAction.REJECT)).dispose()


def test_base_call_map_controller_hook_is_unimplemented() -> None:
    """The map delegates controller creation to the application."""
    with pytest.raises(NotImplementedError):
        BaseCallMap({})._build_controller(None)


def test_configured_next_hop_reads_the_nh_addr_key() -> None:
    """The next hop comes from the sippy global config, not a settings field."""
    call_map = BaseCallMap({"nh_addr": ("127.0.0.1", 15061)})
    assert call_map._configured_next_hop() == ("127.0.0.1", 15061)


def test_configured_next_hop_is_none_when_absent() -> None:
    """No configured hop yields ``None``, which the controller treats as no next hop."""
    assert BaseCallMap({})._configured_next_hop() is None


def test_dispose_disposes_every_known_controller() -> None:
    """The map drops the timers of every call it still knows about."""
    call_map = BaseCallMap({})
    disposed: list[str] = []
    call_map.controllers.extend(
        [
            _RecordingController(PolicyDecision(action=PolicyAction.REJECT), disposed),
            _RecordingController(PolicyDecision(action=PolicyAction.REJECT), disposed),
        ]
    )
    call_map.dispose()
    assert disposed == ["disposed", "disposed"]


def test_base_call_map_rejects_a_source_outside_the_allowlist() -> None:
    """The trunk is untrusted: an unknown source is answered with 403 (AGENT.md section 9)."""
    call_map = BaseCallMap({"nh_addr": ("127.0.0.1", 15061)}, allowed_peers=("10.0.0.1",))
    response, cancel_cb, noack_cb = call_map.recv_request(_FakeRequest(("192.0.2.9", 5060)), None)
    assert response == (403, "Forbidden")
    assert cancel_cb is None
    assert noack_cb is None
