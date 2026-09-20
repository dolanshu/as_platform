# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- The library's own gate: the `Makefile` (`lint`, `type`, `test`, `check`) and the
  `.github/workflows/ci.yml` workflow that runs it (REQ-NF-021).
- The three library-standard documents (REQ-NF-019): `docs/api-reference.md`,
  `docs/integration-guide.md` and `docs/compatibility-matrix.md`.
