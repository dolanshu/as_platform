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

"""Tests for the pure helpers and the sippy adapter boundary of the library.

The helpers here are the library's contract with an application: the outbound Call-ID
derivation, the Request-URI handling and the trunk peer allowlist. The sippy boundary
tests use small fakes, so they run without a signalling stack (REQ-NF-021).
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest

from as_platform.errors import AsError, SkeletonErrorCode
from as_platform.hop import NextHop
from as_platform.sip_adapter import (
    B2BUA_CALL_ID_SUFFIX,
    PASSTHROUGH_HEADERS,
    CallLeg,
    build_request_uri,
    cancel_transaction_timers,
    extract_called_number,
    is_allowed_peer,
    outbound_call_id,
)


def test_outbound_call_id_derives_a_distinct_second_leg_identity() -> None:
    """The outbound leg derives its own dialog identity, never the trunk Call-ID."""
    trunk_call_id = "a5f3c2e1-0001@example.invalid"
    outbound = outbound_call_id(trunk_call_id)
    assert outbound == f"{trunk_call_id}{B2BUA_CALL_ID_SUFFIX}"
    assert outbound != trunk_call_id


def test_b2bua_call_id_suffix_is_the_single_outbound_leg() -> None:
    """The suffix follows sippy's own ``-b2b_%d`` rewrite for the AS's route 1."""
    assert B2BUA_CALL_ID_SUFFIX == "-b2b_1"


def test_extract_called_number_reads_the_user_part_of_a_sip_uri() -> None:
    """The user part of the Request-URI is the called number (RFC 3261 section 19.1)."""
    assert extract_called_number("sip:+8613800138000@10.0.0.1:5060;user=phone") == "+8613800138000"


def test_extract_called_number_strips_uri_parameters() -> None:
    """URI parameters are not part of the number."""
    assert (
        extract_called_number("sip:02161234567@10.0.0.1;user=phone;transport=udp") == "02161234567"
    )


def test_extract_called_number_rejects_a_host_only_uri() -> None:
    """A Request-URI without a user part reports AS-PEER-003, not a host name."""
    with pytest.raises(AsError) as excinfo:
        extract_called_number("sip:10.0.0.1")
    assert excinfo.value.code is SkeletonErrorCode.PEER_MALFORMED_REQUEST


def test_is_allowed_peer_accepts_configured_and_rejects_others() -> None:
    """Only configured trunk peers are accepted (AGENT.md section 9)."""
    assert is_allowed_peer("127.0.0.1", ["127.0.0.1", "10.0.0.1"]) is True
    assert is_allowed_peer("192.0.2.1", ["127.0.0.1"]) is False


def test_passthrough_headers_are_lower_case_and_exclude_stack_owned_headers() -> None:
    """The pass-through set carries no header the stack owns or regenerates."""
    assert all(header == header.lower() for header in PASSTHROUGH_HEADERS)
    owned = {
        "via",
        "route",
        "record-route",
        "contact",
        "max-forwards",
        "from",
        "to",
        "call-id",
        "cseq",
    }
    assert owned.isdisjoint(PASSTHROUGH_HEADERS)
    assert "p-asserted-identity" in PASSTHROUGH_HEADERS


def test_call_leg_is_a_frozen_value_object() -> None:
    """A CallLeg cannot be mutated after construction."""
    leg = CallLeg(leg="in", call_id="c1", local_address="127.0.0.1:5060", remote_address="x")
    assert leg.leg == "in"
    with pytest.raises(dataclasses.FrozenInstanceError):
        leg.leg = "out"  # type: ignore[misc]


def test_build_request_uri_carries_host_port_and_transport() -> None:
    """The outbound Request-URI points at the selected next hop (RFC 3261 section 19.1)."""
    hop = NextHop(name="s-sbc-primary", address="127.0.0.1", port=15061)
    assert (
        build_request_uri("013800138000", hop) == "sip:013800138000@127.0.0.1:15061;transport=udp"
    )


def test_build_request_uri_brackets_an_ipv6_next_hop() -> None:
    """An IPv6 next hop is bracketed as RFC 3261 section 19.1 requires."""
    hop = NextHop(name="v6", address="2001:db8::1", port=5060)
    assert build_request_uri("110", hop) == "sip:110@[2001:db8::1]:5060;transport=udp"


def _fake_timer() -> Any:
    """Build a stand-in for one sippy ``EventListener`` timer.

    Returns:
        A namespace with ``cb_func`` (non-``None`` while armed) and ``cancels`` (how
        often :meth:`cancel` was called). Cancelling clears ``cb_func`` the way ``ED2``
        does when a timer fires, so an already dead timer is recognisable.
    """
    timer: Any = SimpleNamespace(cb_func=object(), cancels=0)

    def cancel() -> None:
        timer.cancels += 1
        timer.cb_func = None

    timer.cancel = cancel
    return timer


def test_cancel_transaction_timers_cancels_armed_timers_then_shuts_the_manager_down() -> None:
    """Both tables' timers are cancelled, then sippy's own manager is shut down.

    ``SipTransactionManager.shutdown()`` cancels only its own cache-purge timer, so the
    per-transaction timers the repository armed survive it; cancelling them first and
    then calling ``shutdown()`` is exactly the stop ordering ``BaseAsStack.stop``
    performs.
    """
    client_timers = {"teA": _fake_timer(), "teB": _fake_timer()}
    # A server transaction only carries the timers its own RFC 3261 branch uses.
    server_timers = {"teA": _fake_timer(), "teD": _fake_timer()}
    client = SimpleNamespace(**client_timers)
    server = SimpleNamespace(**server_timers)
    manager: Any = SimpleNamespace(
        tclient={"invite": client},
        tserver={"invite": server},
        shutdown_calls=0,
    )

    def shutdown() -> None:
        manager.shutdown_calls += 1
        manager.tclient = None
        manager.tserver = None

    manager.shutdown = shutdown

    assert cancel_transaction_timers(manager) == 4
    manager.shutdown()

    for timer in (*client_timers.values(), *server_timers.values()):
        assert timer.cancels == 1, "every armed timer is cancelled exactly once"
        assert timer.cb_func is None
    # The transactions drop their references so nothing can re-arm them either.
    assert client.teA is None and client.teB is None
    assert manager.shutdown_calls == 1


def test_cancel_transaction_timers_is_idempotent() -> None:
    """A second pass finds nothing left to do."""
    manager = SimpleNamespace(tclient={"invite": SimpleNamespace(teA=_fake_timer())}, tserver={})
    assert cancel_transaction_timers(manager) == 1
    assert cancel_transaction_timers(manager) == 0


def test_a_timer_that_already_fired_is_not_cancelled_again() -> None:
    """A one-shot timer whose callback ``ED2`` already ran is dead, not pending."""
    fired = _fake_timer()
    fired.cancel()
    manager = SimpleNamespace(tclient={"invite": SimpleNamespace(teA=fired)}, tserver={})
    assert cancel_transaction_timers(manager) == 0


def test_a_manager_without_transaction_tables_is_tolerated() -> None:
    """``shutdown()`` nulls the transaction tables; walking them must not raise."""
    assert cancel_transaction_timers(SimpleNamespace(tclient=None, tserver=None)) == 0
    assert cancel_transaction_timers(SimpleNamespace()) == 0
