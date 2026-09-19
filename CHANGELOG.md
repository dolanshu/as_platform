# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-19

### Added

- The library repository skeleton: `pyproject.toml` (hatchling, distribution
  `as-platform`, Python 3.10, `sippy==2.4.2`), the `VERSION` file, `README.md`,
  `LICENSE` and `.gitignore`.
- The `as_platform` package (src layout) with its `py.typed` marker — importable and
  typed, but carrying no modules yet. This is step 1 of the staged extraction
  (ADR-0009 decision 7); the shared skeleton moves here in the following steps.
