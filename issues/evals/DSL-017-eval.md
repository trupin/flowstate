# Evaluation: DSL-017

**Date**: 2026-05-10
**Sprint**: sprint-037 (Phase 37c)
**Verdict**: PASS

## E2E Proof-of-Work Audit

| Check | Result | Notes |
|-------|--------|-------|
| Verification log present | PASS | "E2E Verification Log > Post-Implementation Verification" section in issue file has Steps 1 and 2 with commands, observed output, exit codes, and a conclusion. |
| Commands are specific and concrete | PASS | Exact CLI invocations against `tests/dsl/fixtures/valid_worktree_persist.flow` and `tests/dsl/fixtures/invalid_worktree_persist_no_worktree.flow`, with exit-code confirmation. |
| Real E2E (no mocks/TestClient) | PASS | DSL-017 is a parse/type-check change, exercised via the real `uv run flowstate check` CLI (no TestClient, no mocks). Reproduced independently by this evaluator. |
| Scenarios cover acceptance criteria | PASS | Each of the three behavioral acceptance criteria (parse, AST default, WP1) has corresponding evidence; unit tests further cover `worktree_persist=true|false`, the no-attribute default, and the parser-permits/type-checker-rejects split. |
| Server restarted after changes | N/A | DSL-only change; no server runtime path involved. |
| Reproduction logged before fix (bugs) | N/A | DSL-017 is a feature, not a bug. |

## Criteria Results

| # | Criterion | Result | Notes |
|---|-----------|--------|-------|
| 1 | `worktree_persist = true | false` parses at flow level (default: false) | PASS | `test_flow_worktree_persist_true`, `_false`, and `_default_is_false` all pass. Ad-hoc flow with `worktree_persist = false` and another with the attribute omitted both `check` clean (`exit=0`). |
| 2 | AST `Flow` dataclass has `worktree_persist: bool = False` | PASS | `test_flow_worktree_persist_default_is_false` asserts the dataclass default; valid fixture parses with `worktree_persist = true` correctly threaded into the AST per type-checker behaviour. |
| 3 | WP1: `worktree_persist = true` with `worktree = false` → error referencing both attributes | PASS | `uv run flowstate check tests/dsl/fixtures/invalid_worktree_persist_no_worktree.flow` → exit 1, stderr literally contains `WP1`, `worktree_persist`, and `worktree = true`. |
| 4 | All existing tests pass | PASS | `uv run pytest tests/dsl/` → 406 passed in 2.25s; `ruff` clean on `src/flowstate/dsl/` + `tests/dsl/`; `pyright` reports 0 errors. |

## Sprint Contract Tests (Phase 37c, DSL-017 scope)

| Test | Result | Evidence |
|------|--------|----------|
| TEST-37c.1 (`worktree_persist = true` parses; default is `False`) | PASS | Four parser unit tests under `TestWorktreePersistParameter` all green; `test_flow_worktree_persist_default_is_false` confirms the default. The "parses but type-checks false" variant (`test_flow_worktree_persist_true_with_worktree_false_still_parses`) shows the parser is permissive — type checking is a separate stage. |
| TEST-37c.2 (WP1 fires; message references both attribute names) | PASS | Direct `/check` reproduction shows: `Type error: FlowTypeError(rule='WP1', message='worktree_persist = true requires worktree = true (the persist mechanism only applies when worktree isolation is enabled)', location='persist_without_worktree')`. Exit code 1. The message contains the literal substrings `WP1`, `worktree_persist`, and `worktree = true`. |

## Reproduced Evidence

**Targeted unit tests (TEST-37c.1, TEST-37c.2 unit coverage):**
```
$ uv run pytest tests/dsl/ -v -k "worktree_persist or WP1"
... 11 passed, 395 deselected in 0.13s
```
The 11 selected tests cover both parser cases and all WP1 type-checker branches (persist+worktree true/false matrix, default flow, and both fixtures).

**Valid fixture (TEST-37c.1 spirit, CHECK form):**
```
$ uv run flowstate check tests/dsl/fixtures/valid_worktree_persist.flow
OK
exit=0
```

**Invalid fixture (TEST-37c.2):**
```
$ uv run flowstate check tests/dsl/fixtures/invalid_worktree_persist_no_worktree.flow
Type error: FlowTypeError(rule='WP1', message='worktree_persist = true requires worktree = true (the persist mechanism only applies when worktree isolation is enabled)', location='persist_without_worktree')
exit=1
```
Literal-substring grep confirmed: `worktree_persist` (1 occurrence) + `worktree` standalone (2 occurrences) + `WP1` (1 occurrence) — both attribute names are present.

**Default-behaviour regression (worktree_persist=false / omitted):**
```
$ uv run flowstate check /tmp/wp_false.flow   # worktree=false, worktree_persist=false
OK
exit=0

$ uv run flowstate check /tmp/wp_omitted.flow # worktree=false, worktree_persist absent
OK
exit=0
```
Default behaviour is unchanged when `worktree_persist` is omitted or set to `false`, as required.

**Full DSL suite (regression):**
```
$ uv run pytest tests/dsl/
... 406 passed in 2.25s
```

**Lint + type-check:**
```
$ uv run ruff check src/flowstate/dsl/ tests/dsl/
All checks passed!

$ uv run pyright src/flowstate/dsl/
0 errors, 0 warnings, 0 informations
```

## Failures

None.

## Summary

4 of 4 acceptance criteria passed. Both sprint-contract tests in DSL-017's scope (TEST-37c.1 and TEST-37c.2) verified end-to-end through the real CLI. The WP1 error message literally references both `worktree_persist` and `worktree = true` (separated by the verb "requires"), satisfying TEST-37c.2's wording requirement. The parser correctly accepts `worktree_persist = false` and omitting the attribute (default `False`); the type checker only fires WP1 in the specific `persist=true, worktree=false` combination as specified. Full 406-test DSL suite green; lint and type-check clean. Verdict: PASS.
