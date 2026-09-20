# Compatibility matrix

The compatibility contract of `as-platform`. Because the library is consumed from a
filesystem path rather than a registry, neither the consumer's pin nor its lockfile
constrains the library's version (see the integration guide). The contract is this
matrix, enforced by the library's own gate and the consumer's gate running against the
same sibling checkout — a gate failure, not a lock failure.

## Platform

| Item | Value | Notes |
| --- | --- | --- |
| Python | 3.10 only | `requires-python = ">=3.10,<3.11"`. There is no support for 3.9 or 3.11+. |
| Interpreter verified | CPython 3.10 | The reference implementation and this library are run on Python 3.10. |

## Runtime dependencies

| Dependency | Constraint | Why it is a runtime dependency |
| --- | --- | --- |
| `sippy` | `==2.4.2` | The sippy adapter boundary lives in this library. **The pin must not be widened.** Two sippy versions in one interpreter would break the in-process integration and e2e tests, which run the AS and the mock S-SBC in one process. |
| `pydantic` | `>=2.0` | `hop.NextHop` is a pydantic model. |
| `fastapi` | `>=0.110` | `internal_api` serves the console surface as a FastAPI application. |
| `uvicorn[standard]` | `>=0.27` | `internal_api` serves that application on a daemon uvicorn thread. |

The library adds **no** dependency beyond these, and P10 adds none for its two seams
(REQ-NF-020).

## Version

| Item | Value |
| --- | --- |
| Library distribution | `as-platform` |
| Import package | `as_platform` |
| Library version | `0.1.0` (`VERSION` and `pyproject.toml`; `as_platform.__version__` resolves to it) |

The version is resolved by the `distribution → VERSION` chain: installed metadata first,
then the repository `VERSION` file. The two must agree, and the library's suite asserts
that they resolve to `0.1.0` in this checkout.

## Reference implementation

| Item | Value |
| --- | --- |
| Repository | the third-party AS POC, checked out beside this one |
| Distribution | `third-party-as-poc` |
| Version verified against | `0.7.0` |
| Relation | First user of the library. It consumes `as-platform` through
  `[tool.uv.sources] as-platform = { path = "../as_platform", editable = true }`, and its
  unit, integration and e2e layers are the end-to-end guard of a library change. |

When the library changes, the reference implementation follows in the same piece of work:
it is the first user, not a consumer at a distance. A change that breaks its three layers
is a breaking change to the contract above.
