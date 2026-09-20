# API reference

The public surface of the `as-platform` library (distribution `as-platform`, import
package `as_platform`, version `0.1.0`). Every name below is exported by its module's
`__all__`, and every statement is read from the source, not assumed. Anything not named
here is internal.

`as-platform` is a **library, not a service**. It carries the parts of the two AS
instances that are not tied to a use case — the sippy adapter boundary, the error-model
mechanism, the observability surface, the bootstrap plumbing, the controller and stack
shells, and the two pluggable seams — and no use case. An application supplies its own
identity and its own decision; see the integration guide for how to consume it.

Nothing in this package imports `as_app` or `anti_fraud_as` (REQ-F-030). The library's
own suite asserts that independence by parsing every module with `ast`
(`tests/test_library_independence.py`).

## Package layout

| Module | Responsibility |
| --- | --- |
| `as_platform` | the import package; exposes `__version__` |
| `as_platform.version` | the distribution → `VERSION` version chain |
| `as_platform.errors` | the shared error mechanism and the skeleton code family |
| `as_platform.hop` | the `NextHop` value object |
| `as_platform.sip_adapter` | the sippy adapter boundary |
| `as_platform.bootstrap` | port probing and cooperative shutdown |
| `as_platform.transport` | the transport seam |
| `as_platform.state_store` | the state-store seam |
| `as_platform.call_controller` | the B2BUA controller and call-map shells |
| `as_platform.internal_api` | the FastAPI internal API shell |
| `as_platform.main` | the `BaseAsStack` process shell |
| `as_platform.observability.logging` | the structured log |
| `as_platform.observability.metrics` | call counters and dispositions |
| `as_platform.observability.tracing` | per-Call-ID trace and SIP message capture |

## Exports by module

### `as_platform`

- `__version__: str` — the runtime version. It is resolved at import time by the
  `distribution → VERSION` chain below, and is `"0.1.0"` in this checkout.

### `as_platform.version`

- `distribution_version(distribution_name: str) -> str` — the version recorded in the
  installed distribution metadata (`importlib.metadata.version`). Raises
  `PackageNotFoundError` for a source checkout that was never installed.
- `version_file_version(version_file: Path, unknown: str) -> str` — the stripped
  contents of a `VERSION` file, or `unknown` when the file cannot be read.
- `read_version(distribution_name: str, version_file_reader: Callable[[], str]) -> str` —
  the three-step chain: installed metadata first, then `version_file_reader()`, then
  whatever placeholder the reader returns. Metadata comes first because a wheel ships no
  `VERSION` file, so it is the only branch an installed wheel can satisfy.

### `as_platform.errors`

- `ErrorCode` — the **memberless** base of every `AS-*` code family. It carries only the
  constructor that binds a member to `code`, `sip_status` and `message`. It must stay
  memberless, or no family could subclass it.
- `SkeletonErrorCode(ErrorCode)` — the framework's own codes, in three families:
  `AS-CFG-*` (configuration), `AS-PEER-*` (trunk peers) and `AS-INT-*` (internal).
- `SIP_PHRASES: dict[int, str]` — the reason phrases for the status codes an AS can
  emit. sippy puts the phrase it is given on the wire verbatim.
- `sip_status_for(code: ErrorCode) -> int` — the SIP status the code carries.
- `AsError(Exception)` — an application error carrying an `ErrorCode` and structured
  context. Constructed as `AsError(code, detail=None, *, call_id=None, context=None)`.
  Exposes `code`, `detail`, `call_id`, `context`, `sip_status`, `sip_phrase` and
  `as_log_fields()`.

### `as_platform.hop`

- `NextHop(BaseModel)` — a next hop a B2BUA relays towards. Fields: `name: str`,
  `address: str`, `port: int = 5060`, `transport: Literal["udp"] = "udp"`,
  `priority: int = 1`, `description: str = ""`. The model forbids unknown fields. It
  lives in its own module so `sip_adapter` and `call_controller` can both import it
  without a cycle.

### `as_platform.sip_adapter`

- `PASSTHROUGH_HEADERS: tuple[str, ...]` — the headers the B2BUA copies verbatim from the
  trunk leg to the next-hop leg. It excludes everything the stack owns or regenerates
  (`Via`, `Route`, `Record-Route`, `Contact`, `Max-Forwards`, `From`, `To`, `Call-ID`,
  `CSeq`, `Content-Length`).
- `B2BUA_CALL_ID_SUFFIX: str` — `"-b2b_1"`, the suffix the AS appends to the trunk
  Call-ID when it originates the second leg.
- `TRANSACTION_TIMER_NAMES: tuple[str, ...]` — the per-transaction timer attributes of
  sippy's `SipTransaction` (`teA`…`teG`).
- `CallLeg` — a frozen dataclass of one side of a B2BUA call: `leg`, `call_id`,
  `local_address`, `remote_address`.
- `outbound_call_id(trunk_call_id: str) -> str` — `"<trunk Call-ID>-b2b_1"`.
- `extract_called_number(request_uri: str) -> str` — the user part of a SIP or SIPS URI;
  raises `AsError(AS-PEER-003)` when the URI has no user part.
- `is_allowed_peer(source_address: str, allowed_peers: list[str]) -> bool` — the trunk
  peer allowlist check.
- `build_request_uri(number: str, hop: NextHop) -> str` — the outbound Request-URI,
  `sip:<number>@<host>:<port>;transport=udp`, with an IPv6 host bracketed.
- `cancel_transaction_timers(transaction_manager: Any) -> int` — cancels every
  per-transaction timer of a sippy transaction manager and returns how many were
  cancelled. It must run **before** `SipTransactionManager.shutdown()`, which drops the
  transaction tables the timers hang from.

### `as_platform.bootstrap`

- `check_port_available(address: str, port: int, *, family: int = socket.AF_INET) -> None`
  — probes a UDP port before the service starts; raises `AsError(AS-CFG-003)` when it
  cannot be bound.
- `ShutdownController` — the cooperative shutdown state shared between signal handlers
  and the event loop. Exposes `requested`, `reason` and `request(reason)`.
- `install_signal_handlers(controller: ShutdownController) -> None` — installs `SIGTERM`
  and `SIGINT` handlers that only request shutdown; the loop-owned poller in
  `BaseAsStack` is what actually stops the loop.

### `as_platform.transport` — the transport seam

- `Transport` (Protocol) — the local socket the stack binds and sends on. Attributes
  `address: str` and `port: int`, plus `sip_config() -> dict[str, Any]`.
- `UdpTransport` — the only implementation in P10 (ADR-0003). `sip_config()` returns
  exactly `{"_sip_address": self.address, "_sip_port": self.port}`.

No second transport, no TLS and no selection mechanism exist in P10 (REQ-NF-020); P11
adds the second class and, only then, the selection.

### `as_platform.state_store` — the state-store seam

- `StateStore` (Protocol) — where the cross-call caller records are kept, opaque to the
  store. Methods `read(key)`, `write(key, value)` and `trim(prefix, limit)`.
- `InMemoryStateStore` — the only implementation until P11. It holds the records in
  insertion order, re-orders them on every access, and evicts the least recently used
  key first. `trim(prefix, limit)` drops the least recently used keys carrying `prefix`
  above `limit`; it counts **only** the key family its prefix names, because the
  anti-fraud keeps two independent structures with two independent bounds in one store.

The seam sits **under** `CallerStateStore`, which stays in the anti-fraud application
and stays process-level; the window and the ledger are the use case's, the storage is
not (D9).

### `as_platform.call_controller` — the controller shell

- `LEG_TRUNK: str = "trunk"`, `LEG_NEXT_HOP: str = "next_hop"` — the leg names used in
  the `leg` trace attribute.
- `PolicyAction` — `RELAY` or `REJECT`.
- `PolicyDecision` — the one value the application hands the base. Fields: `action`,
  `outbound_event`, `next_hops`, `error`, `disposition` (default `REJECTED`),
  `attributes`, `reject_trace_summary`, `reject_log_message`, `reject_log_fields`,
  `relay_log_message`, `relay_log_fields`. The per-application vocabulary travels in
  these fields rather than in a branch on "which application am I" (REQ-F-031).
- `BaseCallController` — one B2BUA call. See the seam below.
- `BaseCallMap` — the process-wide trunk entry point: it applies the peer allowlist, then
  hands an INVITE to a fresh controller built by the application's `_build_controller`
  hook. Exposes `global_config`, `allowed_peers`, `metrics`, `tracer` and `controllers`,
  plus `recv_request(request, transaction)`, `dispose()` and the two overridable
  rendering hooks `_peer_status_key(hop)` and `_no_answer_hop_label()`.

#### The `BaseCallController` → `decide()` → `PolicyDecision` seam

`apply_call_policy(event)` is the **single seam** where an inbound INVITE is decided on
before it leaves the AS. It calls `self.decide(event)`, remembers the returned
`PolicyDecision` (so a failover attempt re-originates the same event and reproduces the
same trace and log line) and returns it. The base owns the relay, the failover walk, the
timers, the trace, the log, the disposition recording and the peer-status key; the
application owns the decision only.

An application supplies:

- `decide(event) -> PolicyDecision` — required; the base raises `NotImplementedError`.
- `_inbound_fields(request) -> dict[str, Any]` — optional; extra trace/log fields for the
  inbound INVITE, empty by default.
- `_peer_status_key(hop)` / `_no_answer_hop_label()` — optional overrides of the two
  rendering hooks, whose defaults are `"{name}:{address}:{port}"` and the serving hop's
  name.

**The one-leg invariant.** A call may have exactly one leg — `uaA` — for its whole
lifetime: a rejected call is UAS behaviour, not B2BUA, so it never originates a second
leg and `uaO` stays `None` from the first event to the last.

### `as_platform.internal_api` — the internal API shell

- `PayloadProvider` (Protocol) — the per-application data the factory reads. Attributes
  `instance`, `title`, `thread_name`, `resource_path`, `resource_missing`; property
  `ready`; methods `health_extra()` and `resource()`.
- `health_payload(*, version, uptime_seconds, ready, instance, extra=None) -> dict` — the
  `GET /healthz` document. `status` is `"ok"` when ready and `"degraded"` otherwise;
  `rule_set_loaded` is the shared console-contract compatibility key and follows `ready`;
  `extra` is merged after the shared keys.
- `metrics_payload(registry: MetricsRegistry) -> dict` — the `GET /api/v1/metrics`
  document.
- `trace_payload(trace: CallTrace) -> dict` — one call, Call-ID keyed.
- `traces_payload(recorder: TraceRecorder, limit: int = 20) -> dict` — the most recent
  calls.
- `create_internal_api_app(*, version, provider, metrics, tracer, started_at) -> FastAPI`
  — the app factory. It registers `GET /healthz`, `GET /api/v1/metrics`,
  `GET <provider.resource_path>` (503 with `provider.resource_missing` when the resource
  is not loaded), `GET /api/v1/traces`, `GET /api/v1/traces/{call_id}` and
  `WS /ws/events`.
- `InternalApiServer` — the FastAPI/uvicorn server on a daemon thread. Constructed with
  `(address, port, *, version, provider, metrics, tracer)`; exposes `uptime_seconds`,
  `start()` and `stop()`.

### `as_platform.main` — the process shell

- `BaseAsStack` — the sippy process skeleton. See the extension points below.
- `Transport` — re-exported so a subclass can name its transport's type.

#### `BaseAsStack` extension points

An application subclasses `BaseAsStack` and supplies its own instance identity. The base
owns the sippy wiring (`SipConf`, `SipTransactionManager`, `ED2.loop()`), the timers and
the stop ordering.

**Class variables an application must set:**

- `sip_user_agent_name: ClassVar[str]` — the user agent name reported on the trunk.
- `sip_logger_name: ClassVar[str]` — the name handed to sippy's `SipLogger`.

**Class variables it may override:** `bound_log_message` (default
`"signalling stack bound"`), `shutdown_poll_seconds` (default `0.1`) and
`reload_poll_seconds` (default `1.0`).

**The four hooks that raise `NotImplementedError`:**

- `_create_call_map(global_config) -> BaseCallMap` — the application's trunk call map.
- `_create_internal_api_server(address, port) -> InternalApiServer` — the application's
  version and store.
- `_poll_reload() -> None` — reload the application's data file when it changed.
- `_reload_log_fields() -> dict[str, str]` — the reload-source log fields for the event
  loop start line (`rules_file` or `screening_file`).

The base also exposes `start()`, `start_internal_api()`, `run(shutdown)` and `stop()`.

### `as_platform.observability.logging`

- `LogDirection` — `INBOUND = "in"`, `OUTBOUND = "out"`, `INTERNAL = "internal"`, and
  `ALL`.
- `StructuredFormatter(logging.Formatter)` — renders one record as a single-line JSON
  object with the fixed field set.
- `get_logger(name: str) -> logging.Logger`.
- `log_event(logger, level, event, *, call_id=None, direction="-", peer=None, **extra)`
  — emits one structured event.
- `configure_logging(level="INFO", *, structured=True) -> None` — installs the handler on
  the root logger.

The documented field set is `LOG_FIELDS = ("timestamp", "level", "module", "call_id",
"direction", "peer", "event")`; a record with no extra fields renders exactly those
seven.

### `as_platform.observability.metrics`

- `CallDisposition` — `COMPLETED`, `FAILED`, `REJECTED`, `NO_MATCH`, `ABANDONED`.
- `PeerStatus` — `UNKNOWN`, `REACHABLE`, `UNREACHABLE`, `DEGRADED`.
- `MetricsSnapshot` — an immutable view of the counters.
- `MetricsRegistry` — the counters: `calls_total`, `calls_by_disposition`,
  `errors_by_code`, `rule_hits`, `peer_status`, `counters`. Recording methods:
  `record_call_started()`, `record_call_disposition()`, `record_error()`,
  `record_rule_hit()`, `record_counter()`, `set_peer_status()`, plus `snapshot()`.
- `get_metrics_registry() -> MetricsRegistry` — the process-wide registry.

### `as_platform.observability.tracing`

- `TraceEvent` — one observable event on one call leg.
- `CallTrace` — the ordered list of events of one Call-ID.
- `TraceRecorder` — the thread-safe, bounded store of call traces. Constructed with
  `max_calls: int = 200`; `record(...)`, `trace_for(call_id)`, `recent(limit=20)`,
  `known_call_ids()` and `clear()`.
- `get_trace_recorder() -> TraceRecorder` — the process-wide recorder.
- `RecordedSipMessage` — one SIP message as it went over the wire.
- `SipMessageRecorder` — captures every message sippy writes, with `write(...)`,
  `messages_for_any(call_ids)` and `clear()`.

## `py.typed`

The package ships the PEP 561 `py.typed` marker. It is load-bearing: without it a
consumer's `mypy` reports `import-untyped` and its gate fails, so it is part of the
library's definition of done.
