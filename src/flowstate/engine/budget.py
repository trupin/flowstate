"""Budget guard — tracks cumulative execution time against a flow's budget."""


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
