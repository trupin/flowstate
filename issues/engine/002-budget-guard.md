# [ENGINE-002] Budget Guard

## Domain
engine

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: none
- Blocks: ENGINE-005

## Spec References
- specs.md Section 2.7 — "Budget Guard"
- specs.md Section 5.6 — "Budget Guard"
- agents/03-engine.md — "Budget Guard"

## Summary
Implement the BudgetGuard class that tracks cumulative task execution time against a flow's budget. It emits threshold warnings at 75%, 90%, and 95% of the budget, and detects when the budget is exceeded. The budget guard does not kill tasks mid-execution — it signals the executor to pause after the current task completes. This is a pure, stateless utility class with no external dependencies, making it the simplest engine component.

## Acceptance Criteria
- [ ] File `src/flowstate/engine/budget.py` exists and is importable
- [ ] `BudgetGuard` class is implemented with the following interface:
  - `__init__(self, budget_seconds: int)` — stores budget and initializes tracking state
  - `add_elapsed(self, seconds: float) -> list[str]` — adds time, returns list of newly-crossed threshold warnings
  - `exceeded` property (`bool`) — True when elapsed >= budget_seconds
  - `elapsed` attribute (`float`) — current cumulative elapsed seconds
  - `budget_seconds` attribute (`int`) — the configured budget
  - `percent_used` property (`float`) — elapsed / budget_seconds as a fraction (0.0 to 1.0+)
- [ ] Warnings are emitted exactly once per threshold: `"75%"`, `"90%"`, `"95%"`
- [ ] Warnings are returned in order when multiple thresholds are crossed in a single `add_elapsed` call
- [ ] `exceeded` returns `True` when `elapsed >= budget_seconds`
- [ ] `exceeded` returns `False` when `elapsed < budget_seconds`
- [ ] `add_elapsed` can be called with 0 or negative values without crashing (though negative is a no-op)
- [ ] All tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/budget.py` — budget guard implementation
- `tests/engine/test_budget.py` — tests

### Key Implementation Details

```python
class BudgetGuard:
    """Tracks cumulative execution time against a budget.

    Emits threshold warnings at 75%, 90%, and 95%. Detects when budget
    is exceeded. Does NOT enforce the budget — the executor is responsible
    for pausing the flow when `exceeded` becomes True.
    """

    THRESHOLDS = (0.75, 0.90, 0.95)

    def __init__(self, budget_seconds: int) -> None:
        self.budget_seconds = budget_seconds
        self.elapsed: float = 0.0
        self._warned: set[float] = set()

    def add_elapsed(self, seconds: float) -> list[str]:
        """Add task elapsed time. Returns list of threshold warnings crossed.

        Each threshold warning is a string like "75%", "90%", "95%".
        Warnings are returned in ascending order and never repeated.
        """
        if seconds <= 0:
            return []

        self.elapsed += seconds
        warnings: list[str] = []

        for threshold in self.THRESHOLDS:
            if threshold not in self._warned and self.elapsed >= self.budget_seconds * threshold:
                self._warned.add(threshold)
                warnings.append(f"{int(threshold * 100)}%")

        return warnings

    @property
    def exceeded(self) -> bool:
        """True when cumulative elapsed time meets or exceeds the budget."""
        return self.elapsed >= self.budget_seconds

    @property
    def percent_used(self) -> float:
        """Fraction of budget used (0.0 to 1.0+). Can exceed 1.0."""
        if self.budget_seconds <= 0:
            return 1.0
        return self.elapsed / self.budget_seconds
```

### Edge Cases
- **Single large `add_elapsed` call crossing multiple thresholds**: All crossed thresholds should be returned in order. For example, if budget is 100s and `add_elapsed(96)` is called, all three warnings ("75%", "90%", "95%") should be returned.
- **Exact threshold boundary**: `add_elapsed` that brings elapsed to exactly 75% of budget should trigger the warning (using `>=`).
- **Budget of 0 seconds**: `percent_used` returns 1.0, `exceeded` is True immediately. `add_elapsed` returns all warnings on first call.
- **Repeated calls after exceeded**: `add_elapsed` continues to accumulate time but no new warnings are emitted (all thresholds already warned).
- **Negative seconds**: Treated as a no-op — returns empty list, does not modify elapsed.
- **Float precision**: Use `>=` comparison, not `==`. Floating-point rounding should not cause missed thresholds.

## Testing Strategy

Create `tests/engine/test_budget.py`:

1. **test_initial_state** — New BudgetGuard(3600) has elapsed=0.0, exceeded=False, percent_used=0.0.

2. **test_add_elapsed_no_threshold** — Add 100s to a 3600s budget. No warnings returned. elapsed is 100.0.

3. **test_75_percent_warning** — Budget 100s. Add 75s. Returns `["75%"]`.

4. **test_90_percent_warning** — Budget 100s. Add 75s (get 75% warning), then add 15s. Returns `["90%"]`.

5. **test_95_percent_warning** — Budget 100s. Incrementally reach 95s. The 95s call returns `["95%"]`.

6. **test_multiple_thresholds_single_call** — Budget 100s. Add 96s in one call. Returns `["75%", "90%", "95%"]`.

7. **test_exceeded_detection** — Budget 100s. Add 100s. `exceeded` is True.

8. **test_not_exceeded_below_budget** — Budget 100s. Add 99s. `exceeded` is False.

9. **test_exceeded_over_budget** — Budget 100s. Add 150s. `exceeded` is True.

10. **test_warnings_not_repeated** — Budget 100s. Add 76s (get 75% warning). Add 1s. Returns empty list (75% not re-emitted).

11. **test_all_warnings_then_no_more** — Cross all three thresholds. Then add more time. Returns empty list.

12. **test_zero_budget** — Budget 0s. `exceeded` is True immediately. `percent_used` is 1.0.

13. **test_negative_elapsed_ignored** — Add -10s. elapsed stays 0.0. Returns empty list.

14. **test_percent_used** — Budget 200s. Add 50s. percent_used is 0.25. Add 150s. percent_used is 1.0. Add 100s. percent_used is 1.5.

15. **test_exact_boundary** — Budget 100s. Add exactly 75.0s. Returns `["75%"]` (boundary is inclusive via `>=`).
