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

**0.1.0 — extraction complete.** The staged extraction of ADR-0009 decision 7 has moved
the shared skeleton here: the sippy adapter boundary, the error-model mechanism, the
observability surface, the bootstrap plumbing, the controller and stack shells, and the
two pluggable seams (transport and state store). The package is typed, carries its own
test suite (`tests/`), its own gate (`make check`) and its three library-standard
documents (API reference, integration guide, compatibility matrix). The two use cases
stay in the reference repository, and nothing here imports them (REQ-F-030).

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
tests/                the library's own test suite (flat)
docs/                 API reference, integration guide, compatibility matrix
Makefile              the library's own gate (lint, type, test)
.github/workflows/    the CI workflow that runs that gate
```

## Development

The library carries its own gate, so a change to it is verifiable where the library
lives:

```sh
make lint    # ruff format --check + ruff check + mypy
make test    # pytest
make check   # lint + test
```

`make help` lists every target. CI runs the same gate on every push and pull request to
`main`.

## License

Apache-2.0. See `LICENSE`.
