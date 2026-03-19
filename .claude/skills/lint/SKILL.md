---
description: Run all linters and formatters (ruff, pyright, eslint)
user_invocable: true
---

Run all linting and formatting checks for the project.

Check if the user passed `--fix` in their arguments. If so, apply auto-fixes.

**Python checks** (always run):
```bash
# Lint (with --fix if requested)
uv run ruff check . [--fix]
# Format check (or apply if --fix)
uv run ruff format . [--check]  # omit --check if --fix
# Type check
uv run pyright
```

**UI checks** (run only if `ui/node_modules` exists):
```bash
cd ui && npm run lint
```

Report a summary: which checks passed, which failed, and any issues found.
