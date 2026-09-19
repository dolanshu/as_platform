# as-platform

The shared platform skeleton for the third-party IMS/SIP Application Server (AS)
instances.

## What this is

`as-platform` is a **library, not a service**. It carries the parts of the AS instances
that are not tied to a use case — the sippy adapter boundary, the error-model mechanism,
the observability surface, the bootstrap plumbing, the controller and stack shells, and
the pluggable transport and state-store seams — so that two AS instances share one copy
of the skeleton instead of maintaining near-verbatim copies of it.

The use cases stay in the applications. The number-translation AS and the anti-fraud AS
live in the reference repository (`3rtparty_AS_POC`), which consumes this package from a
sibling checkout. This repository is the library; that one is its reference
implementation and first user.

## Status

**0.1.0 — repository skeleton only.** Step 1 of the staged extraction (ADR-0009
decision 7) creates the repository, the meta files and an empty, typed, importable
package. The modules listed above move here in the following steps, and the library's own
test suite, gate and library-standard documents (API reference, integration guide,
compatibility matrix) arrive in step 7.

## Installation

The library is consumed from a sibling checkout, not a registry:

```toml
[project]
dependencies = ["as-platform"]

[tool.uv.sources]
as-platform = { path = "../as_platform", editable = true }
```

`editable = true` is required, not cosmetic: the staged extraction edits the library and
runs the consumer's gate against it, and a `path` source without it installs a copy
rather than linking the checkout (ADR-0009 decision 6).

## Layout

```text
VERSION               the library version
pyproject.toml        hatchling build, `as-platform`, Python 3.10, sippy==2.4.2
src/as_platform/      the import package (src layout), carrying the PEP 561 py.typed marker
```

## License

Apache-2.0. See `LICENSE`.
