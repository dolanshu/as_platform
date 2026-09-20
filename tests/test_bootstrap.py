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

"""Tests for the bootstrap plumbing: port probing and cooperative shutdown.

The signal handlers only request shutdown; the loop-owned poller that reads the flag is
exercised by the consuming repository's integration layer, so no real signal is raised
here (ADR-0002, AGENT.md section 6).
"""

from __future__ import annotations

import signal
import socket

import pytest

from as_platform.bootstrap import (
    ShutdownController,
    check_port_available,
    install_signal_handlers,
)
from as_platform.errors import AsError, SkeletonErrorCode


def test_check_port_available_accepts_a_free_udp_port() -> None:
    """A free loopback port passes the probe without raising."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    check_port_available("127.0.0.1", port)


def test_check_port_available_reports_an_unbindable_address() -> None:
    """A bind failure becomes AS-CFG-003 with the address and port in context."""
    with pytest.raises(AsError) as excinfo:
        check_port_available("192.0.2.1", 5060)
    assert excinfo.value.code is SkeletonErrorCode.CFG_PORT_UNAVAILABLE
    assert excinfo.value.context["port"] == "5060"


def test_shutdown_controller_starts_in_the_running_state() -> None:
    """A fresh controller has no shutdown requested and no reason."""
    controller = ShutdownController()
    assert controller.requested is False
    assert controller.reason is None


def test_shutdown_controller_remembers_the_request_reason() -> None:
    """A request flips the flag and records the trigger."""
    controller = ShutdownController()
    controller.request("SIGTERM")
    assert controller.requested is True
    assert controller.reason == "SIGTERM"


def test_install_signal_handlers_registers_a_handler_for_both_signals() -> None:
    """SIGTERM and SIGINT get a handler instead of the default disposition."""
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    try:
        install_signal_handlers(ShutdownController())
        for signum in (signal.SIGTERM, signal.SIGINT):
            installed = signal.getsignal(signum)
            assert callable(installed)
            assert installed not in (signal.SIG_DFL, signal.SIG_IGN)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
