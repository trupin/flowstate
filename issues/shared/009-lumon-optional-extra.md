# [SHARED-009] Make `lumon` an optional extra; guard all lumon imports

## Domain
shared

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: SHARED-010

## Spec References
- specs.md §13.4 Deployment & Installation — "Lumon sandboxing"

## Summary
`lumon` is currently a private git dependency in `pyproject.toml`. PyPI forbids packages with direct git URLs in their install-requires, which means Flowstate cannot be published as-is. This issue moves `lumon` from `[project.dependencies]` to `[project.optional-dependencies]` as an `[lumon]` extra, and guards every `import lumon` behind a try/except so that core Flowstate (using the ACP / Claude Code harness) works without it. Users who want sandboxing run `pip install "flowstate[lumon]"`.

## Acceptance Criteria
- [ ] `pyproject.toml` has no direct git URL in `[project.dependencies]`.
- [ ] `[project.optional-dependencies]` contains a `lumon` key with the git URL.
- [ ] Every `import lumon` in `src/flowstate/` is wrapped in `try/except ImportError` and sets a module-level flag (`LUMON_AVAILABLE: bool`).
- [ ] At the point where a lumon-requiring harness or sandbox is instantiated, a clear error is raised: `LumonNotInstalledError("Install flowstate[lumon] to enable sandboxed execution.")` with the installation command in the message.
- [ ] The default harness path (ACP / Claude Code) has **zero** lumon imports transitively. Proven by: in a venv with `pip install flowstate` (no extras), `python -c "from flowstate.cli import app"` succeeds without warnings.
- [ ] All existing tests still pass when lumon IS installed (current dev environment).
- [ ] A new test runs in an environment without lumon and verifies that the default code path still works. (Can be simulated by patching `sys.modules["lumon"] = None` in a fixture.)

## Technical Design

### Files to Create/Modify
- `pyproject.toml` — move the lumon line.
- `src/flowstate/engine/lumon.py` — guard all imports; export `LUMON_AVAILABLE`.
- Any other file that imports lumon (likely `src/flowstate/engine/*sandbox*.py` or similar — grep `^import lumon\|^from lumon`).
- `src/flowstate/errors.py` (or wherever typed errors live) — add `LumonNotInstalledError`.
- `tests/engine/test_lumon_optional.py` — new test.

### Key Implementation Details

**`pyproject.toml`:**
```toml
[project]
dependencies = [
    # ... everything except lumon ...
]

[project.optional-dependencies]
lumon = [
    "lumon @ git+ssh://git@github.com/...",
]
```

**`src/flowstate/engine/lumon.py`:**
```python
try:
    import lumon  # noqa: F401
    LUMON_AVAILABLE = True
except ImportError:
    lumon = None  # type: ignore[assignment]
    LUMON_AVAILABLE = False


class LumonNotInstalledError(FlowstateError):
    def __init__(self) -> None:
        super().__init__(
            "Lumon sandboxing is not installed.\n"
            "Install with: pip install 'flowstate[lumon]'"
        )


def require_lumon() -> None:
    if not LUMON_AVAILABLE:
        raise LumonNotInstalledError()
```

Every function inside `lumon.py` that touches `lumon.*` calls `require_lumon()` first.

**Harness factory** (wherever a sandbox harness is picked):
```python
if harness_type == "lumon":
    require_lumon()
    return LumonHarness(...)
```

### Edge Cases
- A flow file declares `harness = "lumon"` but `lumon` is not installed → `flowstate run` fails at flow parse/validate time with `LumonNotInstalledError`, not at subprocess spawn time. Prefer fail-fast.
- `flowstate check` on a lumon-using flow should NOT require lumon (it's static analysis); only execution requires it.
- Type-checker: `pyright` will complain about `lumon = None` being assigned to a module-typed name — use `# type: ignore[assignment]` with a comment, or wrap in a typing `cast`.
- Existing integration tests that exercise lumon should be marked with a pytest marker (e.g., `@pytest.mark.lumon`) and skipped when `LUMON_AVAILABLE` is false.

## Testing Strategy
- New test file `tests/engine/test_lumon_optional.py`:
  - Monkeypatches `sys.modules["lumon"]` to raise on import at module-reimport time.
  - Re-imports `flowstate.engine.lumon`; asserts `LUMON_AVAILABLE is False`.
  - Asserts `require_lumon()` raises `LumonNotInstalledError` with the expected message.
  - Asserts that importing `flowstate.cli` and `flowstate.server.app` does not trigger the lumon import error.
- Mark existing lumon-specific tests with `@pytest.mark.skipif(not LUMON_AVAILABLE, reason="lumon extra not installed")`.

## E2E Verification Plan

### Verification Steps
1. In a clean venv without the `[lumon]` extra: `pip install /path/to/dist/flowstate-0.1.0-py3-none-any.whl`
2. `flowstate --version` → succeeds.
3. `flowstate init` in a scratch dir → succeeds.
4. `flowstate server` → starts, ACP harness works.
5. Attempt to run a flow with `harness = "lumon"` → fails with `LumonNotInstalledError` and the install hint.
6. `pip install 'flowstate[lumon]'` in the same venv → installs lumon.
7. Re-run the lumon flow → now works.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `pyproject.toml` moved lumon to extras
- [ ] All lumon imports guarded
- [ ] `LumonNotInstalledError` defined and raised at harness construction
- [ ] Default path proven lumon-free
- [ ] Existing tests pass
- [ ] New optional test passes
- [ ] `/lint` passes
- [ ] E2E steps above verified
