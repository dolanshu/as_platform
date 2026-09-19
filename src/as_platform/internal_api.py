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

"""The internal REST and WebSocket surface every AS instance serves to the console.

The AS and the console are separate processes: sippy's ``ED2.loop()`` blocks, so it can
never share a thread or an asyncio loop with a web server (ADR-0002). The console talks to
the AS only through this API, never by importing AS modules.

Both applications need the same surface — a health document, the counters, the Call-ID keyed
traces and the live event feed — and differ only in the one resource route they expose
(``/api/v1/rules`` for the number-translation AS, ``/api/v1/screening`` for the anti-fraud)
and in the readiness key that resource implies. That per-application data is supplied as a
:class:`PayloadProvider`; the base carries no branch on "which application am I" (ADR-0009
decision 2, ``docs/architecture/lld.md`` section 11.1).

The AS serves the API from a daemon thread via uvicorn; it only reads snapshots (counters and
traces are lock-guarded), so it never blocks the sippy event loop (ADR-0002, ``AGENT.md``
section 6). CORS is open because the console runs on a different port and the API is a
loopback-only demo surface (ADR-0002, gaps accepted).

Endpoints (ADR-0002)::

    GET /healthz                     — liveness and readiness
    GET /api/v1/metrics              — counters, dispositions, rule hits, peer status
    GET <provider.resource_path>     — the instance's own read-only resource
    GET /api/v1/traces               — most recent calls with their trace events
    GET /api/v1/traces/{call_id}     — one call, Call-ID keyed
    WS  /ws/events                   — live event feed for the console

Nothing here imports an application package (REQ-F-030).
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Protocol

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from as_platform.observability.metrics import MetricsRegistry
from as_platform.observability.tracing import CallTrace, TraceEvent, TraceRecorder

__all__ = [
    "InternalApiServer",
    "PayloadProvider",
    "create_internal_api_app",
    "health_payload",
    "metrics_payload",
    "trace_payload",
    "traces_payload",
]

#: Polling interval, in seconds, of the WebSocket event feed. The TraceRecorder is a
#: passive store (not a pub/sub), so the feed polls it for new calls and pushes the
#: delta. This is a POC simplification — see ``docs/production-gaps.md``.
_WS_POLL_SECONDS = 1.0

#: Maximum number of traces the WebSocket feed sends in one batch.
_WS_MAX_TRACES = 50


class PayloadProvider(Protocol):
    """The per-application data the internal API factory reads.

    The base owns the mechanism — the app factory, the routes, the server thread — and the
    application supplies its own identity, its readiness key and its one resource payload.
    Every member here is read by the factory or by the server, and nothing else is: the
    provider is the whole seam between the shared shell and the instance it serves.
    """

    #: Machine identity reported on ``GET /healthz``, so one console page can say which AS
    #: it is displaying.
    instance: str

    #: FastAPI application title.
    title: str

    #: Name of the uvicorn daemon thread, so a thread dump names the instance.
    thread_name: str

    #: The one resource route path this instance serves (for example ``/api/v1/rules``).
    resource_path: str

    #: The 503 error text for that route when the resource is not loaded.
    resource_missing: str

    @property
    def ready(self) -> bool:
        """Whether the instance's own resource is active, reported as readiness."""
        ...

    def health_extra(self) -> dict[str, Any]:
        """Return the extra health keys this instance adds to the shared document.

        Returns:
            Extra keys merged into the health payload after the shared ones, empty when the
            instance has none.
        """
        ...

    def resource(self) -> dict[str, Any] | None:
        """Return the instance's resource payload.

        Returns:
            The resource document, or ``None`` when it is not loaded — which the route
            answers with a 503.
        """
        ...


def health_payload(
    *,
    version: str,
    uptime_seconds: float,
    ready: bool,
    instance: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the health endpoint payload.

    Args:
        version: Version of the AS.
        uptime_seconds: Seconds since process start.
        ready: Whether the instance's own resource is active.
        instance: Machine identity of the instance answering.
        extra: Extra keys the instance adds after the shared ones; empty when omitted.

    Returns:
        The health document served on ``GET /healthz``.
    """
    return {
        "status": "ok" if ready else "degraded",
        "instance": instance,
        "version": version,
        "uptime_seconds": round(uptime_seconds, 3),
        # ``rule_set_loaded`` is the shared console-contract compatibility key: the one
        # console page reads it for either instance. An instance with a different honest
        # readiness key reports it through ``extra``.
        "rule_set_loaded": ready,
        **(extra or {}),
    }


def metrics_payload(registry: MetricsRegistry) -> dict[str, Any]:
    """Build the statistics payload from the counter registry.

    Args:
        registry: The metrics registry of the AS process.

    Returns:
        The document served on ``GET /api/v1/metrics``.
    """
    snapshot = registry.snapshot()
    return {
        "calls_total": snapshot.calls_total,
        "calls_by_disposition": snapshot.calls_by_disposition,
        "errors_by_code": snapshot.errors_by_code,
        "rule_hits": snapshot.rule_hits,
        "peer_status": snapshot.peer_status,
        # Application-specific counters (for example the anti-fraud screening verdicts).
        # Empty for the number-translation AS; additive, so the console contract holds.
        "counters": snapshot.counters,
    }


def trace_payload(trace: CallTrace) -> dict[str, Any]:
    """Build the Call-ID keyed trace payload.

    Args:
        trace: The trace of one call.

    Returns:
        The document served on ``GET /api/v1/traces/{call_id}``.
    """
    return {
        "call_id": trace.call_id,
        "events": [_event_payload(event) for event in trace.events],
    }


def _event_payload(event: TraceEvent) -> dict[str, Any]:
    """Serialise one trace event.

    Args:
        event: The trace event.

    Returns:
        A JSON-serialisable representation of the event.
    """
    return {
        "timestamp": event.timestamp.isoformat(),
        "call_id": event.call_id,
        "direction": event.direction,
        "method": event.method,
        "peer": event.peer,
        "summary": event.summary,
        "rule_id": event.rule_id,
        "attributes": event.attributes,
    }


def traces_payload(recorder: TraceRecorder, limit: int = 20) -> dict[str, Any]:
    """Build the list of the most recent calls for the console.

    Args:
        recorder: The trace recorder of the AS process.
        limit: Maximum number of calls to include.

    Returns:
        The document served on ``GET /api/v1/traces``.
    """
    return {"calls": [trace_payload(trace) for trace in recorder.recent(limit)]}


def create_internal_api_app(
    *,
    version: str,
    provider: PayloadProvider,
    metrics: MetricsRegistry,
    tracer: TraceRecorder,
    started_at: float,
) -> FastAPI:
    """Create the FastAPI application for the internal API.

    The app factory is the seam between the pure payload builders and the AS process
    state. It closes over the registries so every route handler is a thin read of a
    lock-guarded snapshot (ADR-0002), and over the :class:`PayloadProvider` so the one
    resource route is the instance's own.

    Args:
        version: Version reported by the health endpoint.
        provider: The instance's identity, readiness key and resource payload.
        metrics: Counter registry exposed on ``/api/v1/metrics``.
        tracer: Trace recorder exposed on ``/api/v1/traces``.
        started_at: ``time.monotonic()`` value at server creation, for uptime.

    Returns:
        A FastAPI application with the internal API routes.
    """
    app = FastAPI(title=provider.title, version=version)

    # The console is a separate process on a different port; the API is loopback-only
    # (ADR-0002, gaps accepted). Open CORS lets the browser fetch directly.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        """Liveness and readiness of the AS process.

        Returns:
            The health document with status, version, uptime and readiness.
        """
        return health_payload(
            version=version,
            uptime_seconds=time.monotonic() - started_at,
            ready=provider.ready,
            instance=provider.instance,
            extra=provider.health_extra(),
        )

    @app.get("/api/v1/metrics")
    def get_metrics() -> dict[str, Any]:
        """Counters, dispositions, rule hits and peer status.

        Returns:
            The metrics snapshot document.
        """
        return metrics_payload(metrics)

    # ``response_model=None`` because this handler returns either the resource document or
    # a 503 ``JSONResponse``; without it FastAPI would try to derive a response model from
    # the union annotation and reject it.
    @app.get(provider.resource_path, response_model=None)
    def get_resource() -> dict[str, Any] | JSONResponse:
        """The instance's own read-only resource.

        Returns:
            The resource document, or a 503 when it is not loaded.
        """
        resource = provider.resource()
        if resource is None:
            return JSONResponse({"error": provider.resource_missing}, status_code=503)
        return resource

    @app.get("/api/v1/traces")
    def get_traces() -> dict[str, Any]:
        """Most recent calls with their trace events.

        Returns:
            The traces list document.
        """
        return traces_payload(tracer)

    @app.get("/api/v1/traces/{call_id}")
    def get_trace(call_id: str) -> dict[str, Any]:
        """One call, Call-ID keyed.

        Args:
            call_id: SIP Call-ID of the call.

        Returns:
            The trace document for the call (empty events when unknown).
        """
        return trace_payload(tracer.trace_for(call_id))

    @app.websocket("/ws/events")
    async def ws_events(websocket: WebSocket) -> None:
        """Live event feed for the console.

        Polls the trace recorder for new calls and pushes them as JSON batches. The
        recorder is a passive store, so the feed is pull-based at a fixed interval — a
        POC simplification documented in ``docs/production-gaps.md``.

        Args:
            websocket: The WebSocket connection from the console.
        """
        await websocket.accept()
        seen_call_ids: set[str] = set(tracer.known_call_ids())
        try:
            while True:
                await asyncio.sleep(_WS_POLL_SECONDS)
                current_ids = set(tracer.known_call_ids())
                new_ids = current_ids - seen_call_ids
                if new_ids:
                    recent = tracer.recent(limit=_WS_MAX_TRACES)
                    payload = {
                        "type": "traces",
                        "traces": [trace_payload(t) for t in recent if t.call_id in new_ids],
                    }
                    await websocket.send_json(payload)
                    seen_call_ids = current_ids
        except WebSocketDisconnect:
            pass

    return app


class InternalApiServer:
    """FastAPI/uvicorn internal API server running on a daemon thread.

    The AS process serves its internal API from this server. It runs on a daemon thread
    with its own asyncio event loop (uvicorn installs no signal handlers on non-main
    threads), so it never blocks the sippy event loop (ADR-0002). Every route handler
    only reads lock-guarded snapshots.

    Attributes:
        address: Local address the server binds.
        port: Local TCP port the server binds.
        version: Version reported by the health endpoint.
        provider: The instance's identity, readiness key and resource payload.
        metrics: Counter registry exposed on ``/api/v1/metrics``.
        tracer: Trace recorder exposed on ``/api/v1/traces``.
    """

    def __init__(
        self,
        address: str,
        port: int,
        *,
        version: str,
        provider: PayloadProvider,
        metrics: MetricsRegistry,
        tracer: TraceRecorder,
    ) -> None:
        """Create the internal API server.

        Args:
            address: Local address to bind.
            port: Local TCP port to bind.
            version: Version reported by the health endpoint.
            provider: The instance's identity, readiness key and resource payload.
            metrics: Counter registry.
            tracer: Trace recorder.
        """
        self.address = address
        self.port = port
        self.version = version
        self.provider = provider
        self.metrics = metrics
        self.tracer = tracer
        self.started_at = time.monotonic()
        self._server: Any = None
        self._thread: threading.Thread | None = None

    @property
    def uptime_seconds(self) -> float:
        """Seconds since the server was created."""
        return time.monotonic() - self.started_at

    def start(self) -> None:
        """Bind the port and serve in the background on a daemon thread.

        Raises:
            OSError: When the address and port cannot be bound.
        """
        import uvicorn  # imported lazily: only the AS process runs the server

        app = create_internal_api_app(
            version=self.version,
            provider=self.provider,
            metrics=self.metrics,
            tracer=self.tracer,
            started_at=self.started_at,
        )
        config = uvicorn.Config(
            app,
            host=self.address,
            port=self.port,
            log_level="error",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, name=self.provider.thread_name, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop serving and release the port."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._server = None
