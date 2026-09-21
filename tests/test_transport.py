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

"""Tests for the transport seam and both implementations.

P10 shipped UdpTransport as the only implementation (ADR-0009 decision 5). P11 adds
TlsTransport as the proof that the seam is pluggable (ADR-0010 decision 1, REQ-F-034).
"""

from __future__ import annotations

import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

from as_platform.transport import TlsTransport, UdpTransport


def test_udp_transport_sip_config_names_the_address_and_port() -> None:
    """The seam contributes exactly the two sippy keys the stack built inline before."""
    transport = UdpTransport("127.0.0.1", 5060)
    assert transport.sip_config() == {"_sip_address": "127.0.0.1", "_sip_port": 5060}


def test_udp_transport_keeps_the_binding_it_was_given() -> None:
    """The transport exposes the bind address and port the stack reports."""
    transport = UdpTransport("10.0.0.1", 15060)
    assert transport.address == "10.0.0.1"
    assert transport.port == 15060


def test_udp_transport_start_and_stop_are_no_ops() -> None:
    """UdpTransport doesn't own sockets — sippy manages them. start/stop do nothing."""
    transport = UdpTransport("127.0.0.1", 5060)
    # Should not raise
    transport.start()
    transport.stop()


# ---- TlsTransport tests ----


def _generate_self_signed_cert(tmpdir: Path) -> tuple[Path, Path]:
    """Generate a self-signed cert/key pair for testing.

    Args:
        tmpdir: Directory where the certificate files are written.

    Returns:
        Tuple of (certfile, keyfile) paths.
    """
    certfile = tmpdir / "server.crt"
    keyfile = tmpdir / "server.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(keyfile),
            "-out",
            str(certfile),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=localhost",
        ],
        capture_output=True,
        check=True,
    )
    return certfile, keyfile


def _free_port() -> int:
    """Return an available TCP port on localhost."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_tls_transport_start_allocates_a_local_udp_port() -> None:
    """After start(), sip_config() returns a valid local UDP port for sippy to bind."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        certfile, keyfile = _generate_self_signed_cert(tmpdir)
        tls_port = _free_port()
        transport = TlsTransport("127.0.0.1", tls_port, str(certfile), str(keyfile))
        transport.start()
        try:
            config = transport.sip_config()
            assert config["_sip_address"] == "127.0.0.1"
            assert isinstance(config["_sip_port"], int)
            assert config["_sip_port"] > 0
            # The allocated port matches internal state
            assert config["_sip_port"] == transport._local_udp_port
        finally:
            transport.stop()


def test_tls_transport_sip_config_before_start_raises() -> None:
    """Calling sip_config() before start() is a bug and raises RuntimeError."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        certfile, keyfile = _generate_self_signed_cert(tmpdir)
        transport = TlsTransport("127.0.0.1", _free_port(), str(certfile), str(keyfile))
        with pytest.raises(RuntimeError, match="before start"):
            transport.sip_config()


def test_tls_transport_start_then_stop_releases_resources() -> None:
    """After stop(), the TLS listener port can be rebound by another socket."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        certfile, keyfile = _generate_self_signed_cert(tmpdir)
        tls_port = _free_port()
        transport = TlsTransport("127.0.0.1", tls_port, str(certfile), str(keyfile))
        transport.start()
        transport.stop()
        # Port should now be available
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", tls_port))
        finally:
            s.close()


def test_tls_transport_still_exposes_the_tls_listener_address() -> None:
    """The address/port attributes describe the TLS listener, not the internal UDP socket."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        certfile, keyfile = _generate_self_signed_cert(tmpdir)
        transport = TlsTransport("0.0.0.0", 5061, str(certfile), str(keyfile))
        assert transport.address == "0.0.0.0"
        assert transport.port == 5061


def test_tls_transport_with_cafile_loads_ca_context() -> None:
    """When cafile is provided, the TLS transport enables client certificate verification."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        certfile, keyfile = _generate_self_signed_cert(tmpdir)
        cafile = certfile  # Self-signed cert doubles as its own CA
        transport = TlsTransport(
            "127.0.0.1", _free_port(), str(certfile), str(keyfile), str(cafile)
        )
        transport.start()
        try:
            # Internal state should show cafile was accepted
            assert transport.cafile == str(cafile)
        finally:
            transport.stop()


def test_tls_transport_missing_certfile_is_reported_on_start() -> None:
    """A non-existent cert file produces FileNotFoundError at start time."""
    transport = TlsTransport("127.0.0.1", _free_port(), "/nonexistent.crt", "/nonexistent.key")
    with pytest.raises(FileNotFoundError):
        transport.start()
