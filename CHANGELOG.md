# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-09-20

P11 platform verification — second implementation behind each seam and a capacity harness
(ADR-0010, `docs/phase2-plan.md` section 3). **Never released, tagged or pushed** — the
library is consumed by the POC repository at the sibling checkout `../as_platform`, and
version bumps happen at the item close, not at a release tag.

### Added

- `TlsTransport` in `as_platform.transport`: a TLS/SIPS terminator that bridges encrypted SIP
  to a local sippy UDP socket (ADR-0010 decision 1, REQ-F-034). sippy 2.4.2 has no native
  SIP TLS or TCP support — confirmed by reading `SipTransactionManager.newTransaction()`
  which handles only `udp`, `ws`, `wss` and raises on anything else — so the bridge lives
  **outside** sippy: a Python `ssl` + `socket` listener binds to the configured port,
  creates a paired local UDP socket, and bridges encrypted ↔ decrypted SIP across the gap.
  sippy's internal UDP machinery is untouched; `UdpTransport.start()` / `stop()` are no-ops
  because sippy manages the UDP server itself. The bridge is honest: terminating TLS outside
  the AS is a real deployment pattern (load balancers, SIP proxies) and proves the Transport
  seam is pluggable without pretending sippy supports what it does not.
- `RedisStateStore` in `as_platform.state_store`: the second StateStore implementation,
  using a **background-worker queue** to bridge blocking Redis I/O to sippy threads
  (ADR-0010 decision 3, REQ-F-035, REQ-NF-023). `read` / `write` / `trim` enqueue work
  items and wait on a condition variable; a dedicated worker thread runs the Redis client
  and performs the actual I/O, so sippy callbacks never block (AGENT.md section 6). The
  store stays **optional**: `InMemoryStateStore` remains the default, `make demo` and the
  three application test layers run with no external service (REQ-NF-024). The `trim`
  method is best-effort — Redis has no native per-prefix LRU eviction — matching the
  *purpose* of the in-memory store, not its exact mechanism.
- `as_platform.capacity_harness` module with `CapacityDriver` and `CapacityLevelResult`:
  a library-level load generator and observation tool (ADR-0010 decision 4, REQ-F-037).
  The driver accepts a call factory and drives it at configurable concurrency levels
  (`[1, 4, 8, 16, 32, 64, 128]` by default, customizable per caller). At each level it
  records how many calls completed within the configured timeout, how many timed out, and
  the event-loop gap measured during the level. The harness **does not publish benchmark
  numbers** (D10, REQ-NF-025); its output is structured data passed back to the caller,
  never a "this AS handles X calls per second" claim. It drives through the call-controller
  callback interface (not over sockets) — trades realism for determinism.
- `NextHop.transport` widened from `Literal["udp"]` to `Literal["udp", "tls"]`
  (`as_platform.hop`, REQ-F-036). Extremely backwards-compatible: every existing `"udp"`
  value keeps working, zero consumer code changes needed.
- Transport and StateStore Protocols gained optional `start()` / `stop()` lifecycle methods
  to support implementations that own sockets or threads (`TlsTransport`, `RedisStateStore`).
  `UdpTransport` and `InMemoryStateStore` supply no-op methods. `BaseAsStack.start()` /
  `stop()` in `as_platform.main` now call these.

### Changed

- `BaseAsStack.start()` calls `transport.start()` **before** building `global_config`,
  because `TlsTransport.sip_config()` needs its local UDP port allocated first.
  `BaseAsStack.stop()` calls `transport.stop()` **after** sippy shuts down — sippy's UDP
  socket is released before the TLS bridge tears down.

### Tests

- All P10 boundary guards in `tests/test_seams.py` are rewritten from "one implementation
  each, no TLS/Redis/harness" to "two implementations each, harness module present"
  (`test_the_transport_seam_exports_the_boundary_and_two_implementations`,
  `test_the_state_store_seam_exports_the_boundary_and_two_implementations`,
  `test_the_library_defines_two_transports_and_two_state_stores`,
  `test_the_library_ships_the_capacity_harness_module`). These are deliberate trips that
  P11 rewrites, documented in ACC-P11-005 of this repository's acceptance criteria.
- `tests/test_transport.py` expanded from 2 to 9 tests — full coverage of both
  transports including `TlsTransport` start/stop/sip_config with real self-signed certs.
- `tests/test_state_store.py` expanded from 6 to 15 tests — full coverage of both stores
  including a `RedisStateStore` test class gated on Redis availability (`pytest.skipif`).
- `tests/test_capacity_harness.py` new — 7 tests covering driver configuration, worker
  barrier synchronisation, timeout behaviour and observation field correctness.
- Gate result: **99 passed, 5 skipped** (`make check`, all three layers — format, lint,
  type, pytest). The 5 skipped are RedisStateStore tests in the `TestRedisStateStore`
  class, all gated by `_redis_available()` which checks both module import and
  `redis://localhost:6379` connectivity. The `InMemoryStateStore` and harness tests are
  green, and `UdpTransport` / `InMemoryStateStore` no-op lifecycle tests are green.

### Gaps accepted (inherited from P11 design stage, ADR-0010 gaps)

- **TLS bridge is not SIP TLS (RFC 3261 section 26.2) end-to-end.** sippy still does not
  originate TLS requests — `TlsTransport` covers only the AS listening side, not the AS
  originating side (sippy's `newTransaction()` still rejects `transport='tls'`). This is
  a production gap, not a hidden defect — the bridge proves the Transport seam is
  pluggable; full end-to-end TLS would require subclassing sippy's `Network_server`.
- **WSS not implemented as a transport option.** sippy supports WebSocket Secure but
  both sides would need WebSocket frames; P11 does not add WSS.
- **Capacity harness does not measure end-to-end latency.** It drives through the
  controller callback interface, not over sockets. P9.5's read-only probe remains the
  closest thing to an end-to-end load measurement.
- **P11 ACC row ACC-P11-002 has 5 skipped Redis tests in this environment** — Redis is
  not running on this machine. The guard is correct; the tests pass when Redis is up.

## [0.1.0] - 2026-09-19

The staged extraction of ADR-0009 decision 7, folded into the first version: `0.1.0` has
never been released, tagged or pushed, so the whole extraction is described here rather
than under `[Unreleased]`.

### Added

- The library repository skeleton: `pyproject.toml` (hatchling, distribution
  `as-platform`, Python 3.10, `sippy==2.4.2`), the `VERSION` file, `README.md`,
  `LICENSE` and `.gitignore`.
- The `as_platform` package (src layout) with its PEP 561 `py.typed` marker.
- The shared skeleton moved out of the two AS instances: the `observability` package
  (structured logging, counters and the per-Call-ID trace), the `errors` mechanism (the
  memberless `ErrorCode` base, `SIP_PHRASES`, `sip_status_for`, `AsError` and the
  skeleton `AS-CFG-* / AS-PEER-* / AS-INT-*` family), the `sip_adapter` boundary, the
  `hop` value object, the `bootstrap` plumbing, the `version` chain, the
  `call_controller` shell (`BaseCallController`, `PolicyDecision`, `BaseCallMap`), the
  `internal_api` shell and the `main` process shell (`BaseAsStack`).
- The two pluggable seams, one implementation each: `Transport` / `UdpTransport` and
  `StateStore` / `InMemoryStateStore` (REQ-NF-020; the second implementation of each is
  P11's).
- The library's own test suite (`tests/`), covering the pure helpers, the sippy adapter
  boundary, the two seams, the observability surface, the controller shell, the internal
  API shell and the library-level independence assertion of REQ-F-030.
- The suite's structural boundary guards, added after the first draft (`27fe26e`, `0eeef37`):
  the two seams ship exactly **one** implementation each and no library module is named for a
  second transport (TLS), an external store (Redis) or a capacity harness — a **module-name**
  check, not module content or class names (`REQ-NF-020`, `tests/test_seams.py`); the three
  library-standard documents exist and the application operations set is not copied, and the
  library's own gate carries `ruff`, `mypy` and `pytest` in the `Makefile` recipes, the CI
  `run:` commands and the `pyproject.toml` tool tables (`REQ-NF-019`, `REQ-NF-021`,
  `tests/test_library_standard.py`); and the manifest self-reports `as-platform` with no
  `[tool.uv.workspace]` table, so the library is a standalone distribution rather than a uv
  workspace member (ADR-0009 decision 1).
- The library's own gate: the `Makefile` (`lint`, `type`, `test`, `check`) and the
  `.github/workflows/ci.yml` workflow that runs it (REQ-NF-021).
- The three library-standard documents (REQ-NF-019): `docs/api-reference.md`,
  `docs/integration-guide.md` and `docs/compatibility-matrix.md`.
