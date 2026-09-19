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

"""The shared error model: one mechanism, one code set per family.

Python forbids subclassing an ``Enum`` that has members, so this module owns the
**memberless** :class:`ErrorCode` base and the mechanism built on it — the SIP status
mapping, the reason-phrase table and :class:`AsError`. Each application declares its own
family as a subclass of :class:`ErrorCode`: :class:`SkeletonErrorCode` here carries the
framework's own codes (``AS-CFG-*``, ``AS-PEER-*``, ``AS-INT-*``), and an application
package carries its use-case vocabulary. Every code, SIP status and log message is
byte-identical to the single ``AsErrorCode`` it replaces, so no observable behaviour moves
(REQ-F-031, ADR-0009 decision 3).

:class:`AsError` is typed on :class:`ErrorCode`, so it carries a member of any family;
nothing here knows which family an error came from, and nothing here may import an
application package (REQ-F-030).
"""

from __future__ import annotations

from enum import Enum
from typing import Final

__all__ = ["AsError", "ErrorCode", "SIP_PHRASES", "SkeletonErrorCode", "sip_status_for"]


class ErrorCode(Enum):
    """Memberless base of every ``AS-*`` code family.

    It carries **no members** — a member would make the enum non-subclassable — and only the
    shared constructor that binds a member to its code, SIP status and log message. Each
    family subclasses it and declares its own rows.
    """

    def __init__(self, code: str, sip_status: int, message: str) -> None:
        """Bind the enum member to its code, SIP status and default log message.

        Args:
            code: Stable, human readable identifier such as ``AS-ROUTE-001``.
            sip_status: SIP status code used when the failure is reported on the trunk.
            message: Default log message, written in lower case English.
        """
        self.code: Final[str] = code
        self.sip_status: Final[int] = sip_status
        self.message: Final[str] = message


class SkeletonErrorCode(ErrorCode):
    """The framework's own codes: configuration, trunk peers and internal failure.

    These are the failures any AS instance can hit while starting or while handling the
    trunk, independent of its use case, so the skeleton owns them (ADR-0009 decision 3).
    """

    # --- configuration -----------------------------------------------------
    CFG_MISSING = ("AS-CFG-001", 500, "required configuration value is missing")
    CFG_INVALID = ("AS-CFG-002", 500, "configuration value failed validation")
    CFG_PORT_UNAVAILABLE = ("AS-CFG-003", 500, "signalling port cannot be bound")
    CFG_PEER_INVALID = ("AS-CFG-004", 500, "trunk peer configuration is not usable")

    # --- trunk peers -------------------------------------------------------
    PEER_NOT_ALLOWED = ("AS-PEER-001", 403, "source address is not an allowed trunk peer")
    PEER_UNREACHABLE = ("AS-PEER-002", 503, "next hop peer did not answer")
    PEER_MALFORMED_REQUEST = ("AS-PEER-003", 400, "request from the trunk could not be parsed")

    # --- internal ----------------------------------------------------------
    INTERNAL_ERROR = ("AS-INT-001", 500, "unexpected internal failure")


#: Reason phrases for the status codes an AS can emit (RFC 3261 section 21, RFC 8688
#: section 3 for 608). sippy puts the phrase it is given on the wire verbatim, so this map
#: is the only thing that makes a rejected call read as ``608 Rejected`` instead of falling
#: back to ``Server Internal Error`` (ADR-0007, LLD section 9.5). It lives with the
#: mechanism, once, so the phrase cannot drift between families.
SIP_PHRASES: Final[dict[int, str]] = {
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    480: "Temporarily Unavailable",
    500: "Server Internal Error",
    503: "Service Unavailable",
    603: "Decline",
    608: "Rejected",
}


def sip_status_for(code: ErrorCode) -> int:
    """Return the SIP status code that reports the given internal error.

    Args:
        code: Internal error identifier, from any family.

    Returns:
        The SIP status code from the error model.
    """
    return code.sip_status


class AsError(Exception):
    """Application error carrying an :class:`ErrorCode` and structured context.

    The context dictionary is logged verbatim; it must never contain payload bodies or
    credentials (``AGENT.md`` section 9). The code may belong to any family, because the
    error carries the mechanism, not the vocabulary.
    """

    def __init__(
        self,
        code: ErrorCode,
        detail: str | None = None,
        *,
        call_id: str | None = None,
        context: dict[str, str] | None = None,
    ) -> None:
        """Create an application error.

        Args:
            code: Internal error identifier, from any family.
            detail: Optional specific explanation; falls back to the code message.
            call_id: SIP Call-ID of the affected call, when known.
            context: Extra structured fields for the log line.
        """
        super().__init__(detail or code.message)
        self.code = code
        self.detail = detail or code.message
        self.call_id = call_id
        self.context: dict[str, str] = dict(context or {})

    @property
    def sip_status(self) -> int:
        """SIP status code used to report this failure on the trunk."""
        return self.code.sip_status

    @property
    def sip_phrase(self) -> str:
        """Reason phrase belonging to :attr:`sip_status`."""
        return SIP_PHRASES.get(self.sip_status, "Server Internal Error")

    def as_log_fields(self) -> dict[str, str]:
        """Render the error as the structured fields of a log line.

        Returns:
            A mapping with the error code, the SIP status and the detail text.
        """
        fields = {
            "error_code": self.code.code,
            "sip_status": str(self.sip_status),
            "error_detail": self.detail,
        }
        fields.update(self.context)
        return fields
