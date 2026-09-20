# Integration guide

How an application consumes `as-platform`. The reference implementation is the
third-party AS POC (`third-party-as-poc`), checked out beside this repository; it is the
library's first user and follows this guide. A new consumer follows the same steps.

## 1. Depend on the library from a sibling checkout

The library is consumed from a sibling checkout, not a registry. In the consumer's
`pyproject.toml`:

```toml
[project]
dependencies = ["as-platform"]

[tool.uv.sources]
as-platform = { path = "../as_platform", editable = true }
```

**`editable = true` is mandatory, not cosmetic.** A `path` source with no `editable` key
(or `editable = false`) installs a **copy** of the checkout rather than linking it, so a
change to the library would not be visible to the consumer's gate until a re-sync. Only
`editable = true` links the checkout, which is what makes a change to the library
verifiable where the library lives (REQ-NF-021) and where the consumer runs its own gate.

The path is relative to the consuming `pyproject.toml`, so `../as_platform` means a
sibling of the consumer's root. Clone both repositories side by side; a checkout of only
one of them does not resolve.

**The version is not a guard.** A `path` source ignores a version constraint in
`[project.dependencies]`, and `uv sync --frozen` accepts a version skew, so neither the
pin nor the lockfile constrains the library. The contract is the compatibility matrix,
enforced by the two gates running against the same sibling checkout. A consumer that
needs a versioned dependency publishes the library and consumes it by version; that is
out of scope for this POC.

## 2. What an application must subclass and provide

The library carries the mechanism; the application carries its use case. An application:

- **Subclasses `BaseAsStack`** (`as_platform.main`) and sets `sip_user_agent_name` and
  `sip_logger_name`. It implements the four hooks the base leaves open:
  `_create_call_map`, `_create_internal_api_server`, `_poll_reload` and
  `_reload_log_fields`.
- **Subclasses `BaseCallController`** (`as_platform.call_controller`) and implements the
  one decision hook, `decide(event) -> PolicyDecision`. The base owns the relay, the
  failover walk, the timers, the trace and the log.
- **Subclasses `BaseCallMap`** and implements `_build_controller(next_hop)` to create its
  controller.
- **Supplies a `PayloadProvider`** (`as_platform.internal_api`) with its identity, its
  readiness key and its one resource route.
- **Declares its own error family** as a subclass of `ErrorCode`
  (`as_platform.errors`), next to the skeleton's `SkeletonErrorCode`.
- **Chooses a transport and a state store.** In P10 both seams have exactly one
  implementation each — `UdpTransport` and `InMemoryStateStore` — so an application uses
  those; P11 adds the second implementation of each.

Everything the application supplies is data or a decision, never a branch on "which
application am I": the per-application trace and log vocabulary travels inside
`PolicyDecision`, so the base reproduces the application's lines without knowing it.

## 3. The test boundary: `BaseAsStack` is not unit-tested here

The library's own suite covers the pure helpers, the sippy adapter boundary, the two
seams, the observability surface, the controller shell and the internal API shell. It
does **not** test `BaseAsStack`'s socket binding, and this is a deliberate boundary, not
a gap that was silently skipped.

`BaseAsStack.start()` binds a real UDP socket and `BaseAsStack.run()` calls sippy's
blocking `ED2.loop()`. A test of either needs a second SIP endpoint and the process-wide
`ED2` dispatcher, which is exactly the "AS and mock S-SBC on UDP, a full call" shape that
`AGENT.md` section 11 assigns to the **application's** integration and e2e layers, not to
a library's suite (REQ-NF-021, ADR-0009 decision 8). The library therefore tests the
stack's seams and the ordering it documents — for example that
`cancel_transaction_timers` cancels the timers the repository armed before sippy's own
manager is shut down — and leaves the bound socket to the consumer.

**The consumer's obligation follows from that boundary.** A consumer that changes
`BaseAsStack`, or the controller shell it drives, must run its own integration and e2e
layers against a real peer, because the library's suite cannot see a socket. The
reference implementation does exactly that: its integration and e2e layers place calls
against a mock S-SBC on loopback UDP.

## 4. What a library-only consumer must bring itself

The library carries **no mock S-SBC and no console**. A consumer that wants to run a call
end to end, or to watch one, brings its own:

- **Its own trunk peer.** The library's tests use fakes and never open a signalling
  socket, so a consumer that exercises a real call needs a peer that speaks SIP on the
  trunk. The reference implementation's mock S-SBC stays in that repository.
- **Its own UI.** The internal API (`as_platform.internal_api`) serves JSON and a
  WebSocket feed; the library ships no page. The reference implementation's console stays
  in that repository.

This is the same boundary `AGENT.md` section 1 states: the project implements the
external AS; the S-SBC and the console are the consumer's.

## 5. Running the library's own gate

The library carries its own gate, so a change to it is verifiable where it lives:

```sh
make lint    # ruff format --check + ruff check + mypy
make test    # pytest
make check   # lint + test
```

`make help` lists every target. The gate is also run by
`.github/workflows/ci.yml` on every push and pull request to `main`.
