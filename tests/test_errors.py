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

"""Tests for the shared error mechanism: the code families, the SIP mapping and AsError."""

from __future__ import annotations

from as_platform.errors import (
    SIP_PHRASES,
    AsError,
    ErrorCode,
    SkeletonErrorCode,
    sip_status_for,
)


def test_the_skeleton_family_carries_the_three_code_families() -> None:
    """The framework's own codes are CFG, PEER and INT (ADR-0009 decision 3)."""
    prefixes = {member.code.split("-")[1] for member in SkeletonErrorCode}
    assert prefixes == {"CFG", "PEER", "INT"}


def test_every_skeleton_code_has_a_unique_identifier() -> None:
    """No two members share a code."""
    codes = [member.code for member in SkeletonErrorCode]
    assert len(codes) == len(set(codes))


def test_every_skeleton_code_has_a_reason_phrase() -> None:
    """Every status the skeleton emits has a phrase, so sippy puts it on the wire."""
    for member in SkeletonErrorCode:
        assert member.sip_status in SIP_PHRASES


def test_the_error_code_base_stays_memberless() -> None:
    """The base must carry no members, or no family could subclass it."""
    assert list(ErrorCode) == []


def test_sip_status_for_returns_the_code_status() -> None:
    """The mapping helper reads the SIP status the code carries."""
    assert sip_status_for(SkeletonErrorCode.PEER_NOT_ALLOWED) == 403
    assert sip_status_for(SkeletonErrorCode.PEER_UNREACHABLE) == 503


def test_as_error_defaults_its_detail_to_the_code_message() -> None:
    """A bare error carries the code's default message."""
    error = AsError(SkeletonErrorCode.INTERNAL_ERROR)
    assert error.detail == SkeletonErrorCode.INTERNAL_ERROR.message
    assert str(error) == SkeletonErrorCode.INTERNAL_ERROR.message


def test_as_error_reports_the_sip_status_and_phrase() -> None:
    """The error answers with the code's SIP status and the matching phrase."""
    error = AsError(SkeletonErrorCode.PEER_NOT_ALLOWED)
    assert error.sip_status == 403
    assert error.sip_phrase == "Forbidden"


def test_as_error_log_fields_merge_the_context() -> None:
    """The structured fields carry the code, the status, the detail and the context."""
    error = AsError(
        SkeletonErrorCode.CFG_INVALID,
        "bad port",
        call_id="c1",
        context={"field": "port"},
    )
    fields = error.as_log_fields()
    assert fields["error_code"] == "AS-CFG-002"
    assert fields["sip_status"] == "500"
    assert fields["error_detail"] == "bad port"
    assert fields["field"] == "port"
