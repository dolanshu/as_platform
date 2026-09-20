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

"""Tests for the observability surface: counters, traces and the structured log."""

from __future__ import annotations

import json
import logging

from as_platform.observability.logging import (
    LOG_FIELDS,
    LogDirection,
    StructuredFormatter,
    log_event,
)
from as_platform.observability.metrics import (
    CallDisposition,
    MetricsRegistry,
    PeerStatus,
)
from as_platform.observability.tracing import TraceRecorder


def test_metrics_registry_counts_calls_errors_rules_and_peers() -> None:
    """Every counter the console reads is recorded and snapshotted."""
    registry = MetricsRegistry()
    registry.record_call_started()
    registry.record_call_disposition(CallDisposition.COMPLETED)
    registry.record_error("AS-PEER-001")
    registry.record_rule_hit("R-010")
    registry.record_counter("verdict.reject")
    registry.set_peer_status("s-sbc:127.0.0.1:5060", PeerStatus.REACHABLE)
    snapshot = registry.snapshot()
    assert snapshot.calls_total == 1
    assert snapshot.calls_by_disposition == {"completed": 1}
    assert snapshot.errors_by_code == {"AS-PEER-001": 1}
    assert snapshot.rule_hits == {"R-010": 1}
    assert snapshot.counters == {"verdict.reject": 1}
    assert snapshot.peer_status == {"s-sbc:127.0.0.1:5060": "reachable"}


def test_metrics_snapshot_is_a_copy_not_a_live_view() -> None:
    """A later write does not change an already-taken snapshot."""
    registry = MetricsRegistry()
    registry.record_call_started()
    snapshot = registry.snapshot()
    registry.record_call_started()
    assert snapshot.calls_total == 1


def test_trace_recorder_keys_events_by_call_id() -> None:
    """Every event of one call is retained under its Call-ID, in order."""
    recorder = TraceRecorder()
    recorder.record("call-1", LogDirection.INBOUND, "INVITE", "inbound invite")
    recorder.record("call-2", LogDirection.INBOUND, "INVITE", "other call")
    recorder.record("call-1", LogDirection.OUTBOUND, "INVITE", "outbound invite")
    trace = recorder.trace_for("call-1")
    assert trace.call_id == "call-1"
    assert [event.summary for event in trace.events] == ["inbound invite", "outbound invite"]


def test_trace_recorder_bounds_its_retention() -> None:
    """The oldest Call-ID is dropped once the bound is reached."""
    recorder = TraceRecorder(max_calls=2)
    for call_id in ("c1", "c2", "c3"):
        recorder.record(call_id, LogDirection.INBOUND, "INVITE", "invite")
    assert recorder.known_call_ids() == ["c3", "c2"]
    assert recorder.trace_for("c1").events == []


def test_trace_for_an_unknown_call_id_is_empty() -> None:
    """An unknown Call-ID yields an empty trace rather than raising."""
    assert TraceRecorder().trace_for("missing").events == []


def test_structured_formatter_emits_the_documented_field_set() -> None:
    """A plain record renders exactly the seven documented fields (LLD section 4)."""
    record = logging.LogRecord(
        name="as_platform.demo",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="invite received",
        args=(),
        exc_info=None,
    )
    payload = json.loads(StructuredFormatter().format(record))
    assert set(payload) == set(LOG_FIELDS)


def test_log_event_sets_the_call_id_direction_and_peer_fields() -> None:
    """The event wrapper fills the correlation fields of the log line."""
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        """A handler that keeps the records it is given."""

        def emit(self, record: logging.LogRecord) -> None:
            """Append the record to the enclosing list."""
            captured.append(record)

    logger = logging.getLogger("as_platform.test.log_event")
    logger.handlers = [_Capture()]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        log_event(
            logger,
            logging.INFO,
            "invite received",
            call_id="c1",
            direction=LogDirection.INBOUND,
            peer="127.0.0.1:5060",
        )
    finally:
        logger.handlers = []

    assert len(captured) == 1
    assert captured[0].call_id == "c1"
    assert captured[0].direction == "in"
    assert captured[0].peer == "127.0.0.1:5060"
