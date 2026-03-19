---
description: Run the test suite with optional module or test name filter
user_invocable: true
---

Run the flowstate test suite.

Parse the user's arguments:
- No args → run all tests: `uv run pytest`
- Module name (e.g., "dsl", "state", "engine", "server") → `uv run pytest tests/<module>/`
- Test name (e.g., "test_parser") → `uv run pytest -k "<test_name>"`
- Both (e.g., "dsl test_parser") → `uv run pytest tests/dsl/ -k "test_parser"`

Add `-v` for verbose output so test names are visible.

Report the results: number of tests passed/failed, and show any failure details.
