"""Tests for BudgetGuard — threshold warnings and budget exceeded detection."""

from flowstate.engine.budget import BudgetGuard


class TestBudgetGuard:
    def test_initial_state(self) -> None:
        """New BudgetGuard(3600) has elapsed=0.0, exceeded=False, percent_used=0.0."""
        guard = BudgetGuard(3600)
        assert guard.elapsed == 0.0
        assert guard.exceeded is False
        assert guard.percent_used == 0.0
        assert guard.budget_seconds == 3600

    def test_add_elapsed_no_threshold(self) -> None:
        """Add 100s to a 3600s budget. No warnings returned. elapsed is 100.0."""
        guard = BudgetGuard(3600)
        warnings = guard.add_elapsed(100)
        assert warnings == []
        assert guard.elapsed == 100.0

    def test_75_percent_warning(self) -> None:
        """Budget 100s. Add 75s. Returns ["75%"]."""
        guard = BudgetGuard(100)
        warnings = guard.add_elapsed(75)
        assert warnings == ["75%"]

    def test_90_percent_warning(self) -> None:
        """Budget 100s. Add 75s (get 75% warning), then add 15s. Returns ["90%"]."""
        guard = BudgetGuard(100)
        guard.add_elapsed(75)
        warnings = guard.add_elapsed(15)
        assert warnings == ["90%"]

    def test_95_percent_warning(self) -> None:
        """Budget 100s. Incrementally reach 95s. The 95s call returns ["95%"]."""
        guard = BudgetGuard(100)
        guard.add_elapsed(75)  # triggers 75%
        guard.add_elapsed(15)  # triggers 90%
        warnings = guard.add_elapsed(5)  # triggers 95%
        assert warnings == ["95%"]

    def test_multiple_thresholds_single_call(self) -> None:
        """Budget 100s. Add 96s in one call. Returns ["75%", "90%", "95%"]."""
        guard = BudgetGuard(100)
        warnings = guard.add_elapsed(96)
        assert warnings == ["75%", "90%", "95%"]

    def test_exceeded_detection(self) -> None:
        """Budget 100s. Add 100s. exceeded is True."""
        guard = BudgetGuard(100)
        guard.add_elapsed(100)
        assert guard.exceeded is True

    def test_not_exceeded_below_budget(self) -> None:
        """Budget 100s. Add 99s. exceeded is False."""
        guard = BudgetGuard(100)
        guard.add_elapsed(99)
        assert guard.exceeded is False

    def test_exceeded_over_budget(self) -> None:
        """Budget 100s. Add 150s. exceeded is True."""
        guard = BudgetGuard(100)
        guard.add_elapsed(150)
        assert guard.exceeded is True

    def test_warnings_not_repeated(self) -> None:
        """Budget 100s. Add 76s (get 75% warning). Add 1s. Returns empty list."""
        guard = BudgetGuard(100)
        warnings_first = guard.add_elapsed(76)
        assert warnings_first == ["75%"]
        warnings_second = guard.add_elapsed(1)
        assert warnings_second == []

    def test_all_warnings_then_no_more(self) -> None:
        """Cross all three thresholds. Then add more time. Returns empty list."""
        guard = BudgetGuard(100)
        guard.add_elapsed(96)  # triggers all three
        warnings = guard.add_elapsed(50)
        assert warnings == []

    def test_zero_budget(self) -> None:
        """Budget 0s. exceeded is True immediately. percent_used is 1.0."""
        guard = BudgetGuard(0)
        assert guard.exceeded is True
        assert guard.percent_used == 1.0

    def test_negative_elapsed_ignored(self) -> None:
        """Add -10s. elapsed stays 0.0. Returns empty list."""
        guard = BudgetGuard(100)
        warnings = guard.add_elapsed(-10)
        assert warnings == []
        assert guard.elapsed == 0.0

    def test_percent_used(self) -> None:
        """Budget 200s. Add 50s -> 0.25. Add 150s -> 1.0. Add 100s -> 1.5."""
        guard = BudgetGuard(200)
        guard.add_elapsed(50)
        assert guard.percent_used == 0.25
        guard.add_elapsed(150)
        assert guard.percent_used == 1.0
        guard.add_elapsed(100)
        assert guard.percent_used == 1.5

    def test_exact_boundary(self) -> None:
        """Budget 100s. Add exactly 75.0s. Returns ["75%"] (boundary is inclusive via >=)."""
        guard = BudgetGuard(100)
        warnings = guard.add_elapsed(75.0)
        assert warnings == ["75%"]
