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

"""Tests for the internal API shell: the health payload and the app factory.

``fastapi.testclient`` needs ``httpx``, which is not a dependency of this library, so the
app factory is exercised through its route table and its route handlers rather than
through an HTTP client (REQ-NF-021: no new dependency).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from as_platform.internal_api import (
    PayloadProvider,
    create_internal_api_app,
    health_payload,
    metrics_payload,
)
from as_platform.observability.metrics import MetricsRegistry
from as_platform.observability.tracing import TraceRecorder


class _FakeProvider:
    """A minimal application: identity, readiness key and one resource payload."""

    instance = "test-as"
    title = "Test AS internal API"
    thread_name = "test-as-api"
    resource_path = "/api/v1/widgets"
    resource_missing = "widgets are not loaded"

    def __init__(self, *, ready: bool, resource: dict[str, Any] | None) -> None:
        """Remember the readiness and the resource the fake serves."""
        self._ready = ready
        self._resource = resource

    @property
    def ready(self) -> bool:
        """Whether the fake's resource is active."""
        return self._ready

    def health_extra(self) -> dict[str, Any]:
        """Return one extra health key, as an instance with a different key would."""
        return {"widgets_loaded": self._ready}

    def resource(self) -> dict[str, Any] | None:
        """Return the fake's resource payload."""
        return self._resource


def _app(provider: PayloadProvider) -> FastAPI:
    """Build the internal API app for a provider with an empty registry and tracer.

    Args:
        provider: The instance's identity, readiness key and resource payload.

    Returns:
        The FastAPI application built by the factory.
    """
    return create_internal_api_app(
        version="0.1.0",
        provider=provider,
        metrics=MetricsRegistry(),
        tracer=TraceRecorder(),
        started_at=0.0,
    )


def _endpoint(app: FastAPI, path: str) -> Any:
    """Return the route handler registered for a path.

    Args:
        app: The application to search.
        path: The route path to look up.

    Returns:
        The route endpoint callable.
    """
    for route in app.routes:
        if route.path == path:
            return route.endpoint
    raise AssertionError(f"no route registered for {path}")


def test_health_payload_reports_ok_when_ready() -> None:
    """Readiness drives the status, and the compatibility key follows it."""
    payload = health_payload(
        version="0.1.0", uptime_seconds=3.14159, ready=True, instance="test-as"
    )
    assert payload["status"] == "ok"
    assert payload["rule_set_loaded"] is True
    assert payload["instance"] == "test-as"
    assert payload["version"] == "0.1.0"
    assert payload["uptime_seconds"] == 3.142


def test_health_payload_reports_degraded_when_not_ready() -> None:
    """An inactive resource degrades the instance."""
    payload = health_payload(version="0.1.0", uptime_seconds=0.0, ready=False, instance="test-as")
    assert payload["status"] == "degraded"
    assert payload["rule_set_loaded"] is False


def test_health_payload_merges_the_instance_extra_keys() -> None:
    """An instance with a different honest readiness key reports it through ``extra``."""
    payload = health_payload(
        version="0.1.0",
        uptime_seconds=0.0,
        ready=True,
        instance="test-as",
        extra={"widgets_loaded": True},
    )
    assert payload["widgets_loaded"] is True


def test_the_app_factory_registers_the_documented_routes() -> None:
    """The shell serves health, metrics, the instance resource and the traces."""
    app = _app(_FakeProvider(ready=True, resource={}))
    paths = {route.path for route in app.routes}
    for expected in (
        "/healthz",
        "/api/v1/metrics",
        "/api/v1/widgets",
        "/api/v1/traces",
        "/api/v1/traces/{call_id}",
        "/api/v1/traces/{call_id}/messages",
        "/ws/events",
    ):
        assert expected in paths


def test_the_app_factory_uses_the_provider_identity() -> None:
    """The title comes from the provider, so the docs and a thread dump name it."""
    app = _app(_FakeProvider(ready=True, resource={}))
    assert app.title == "Test AS internal API"


def test_the_health_route_returns_the_health_payload() -> None:
    """The route handler is a thin read of the provider's readiness."""
    app = _app(_FakeProvider(ready=False, resource=None))
    payload = _endpoint(app, "/healthz")()
    assert payload["status"] == "degraded"
    assert payload["instance"] == "test-as"


def test_the_resource_route_serves_the_provider_resource() -> None:
    """A loaded resource is returned as the route payload."""
    app = _app(_FakeProvider(ready=True, resource={"items": [1]}))
    assert _endpoint(app, "/api/v1/widgets")() == {"items": [1]}


def test_the_resource_route_answers_503_when_not_loaded() -> None:
    """An absent resource is answered with the provider's 503 text."""
    app = _app(_FakeProvider(ready=False, resource=None))
    response = _endpoint(app, "/api/v1/widgets")()
    assert isinstance(response, JSONResponse)
    assert response.status_code == 503


def test_metrics_payload_exposes_the_counters() -> None:
    """The metrics route reads a lock-guarded snapshot of the registry."""
    registry = MetricsRegistry()
    registry.record_call_started()
    payload = metrics_payload(registry)
    assert payload["calls_total"] == 1
