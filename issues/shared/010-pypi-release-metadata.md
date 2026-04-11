# [SHARED-010] PyPI release pipeline + `pyproject.toml` metadata

## Domain
shared

## Status
todo

## Priority
P1 (important)

## Dependencies
- Depends on: SHARED-008, SHARED-009
- Blocks: SHARED-011

## Spec References
- specs.md §13.4 Deployment & Installation

## Summary
With the UI bundled (SHARED-008) and lumon optional (SHARED-009), Flowstate can be published to PyPI. This issue fills out the `pyproject.toml` metadata that PyPI requires for a professional listing, documents the manual release procedure in `RELEASING.md`, and does a dry-run release to TestPyPI to validate the whole pipeline before anything goes to production PyPI.

## Acceptance Criteria
- [ ] `pyproject.toml` `[project]` block is fully populated:
  - `name`, `version` (pinned to `0.1.0` for the first release)
  - `description` (one-liner)
  - `readme = "README.md"`
  - `license = { text = "MIT" }` (or the actual chosen license — confirm before writing)
  - `authors` / `maintainers`
  - `requires-python = ">=3.12"`
  - `keywords = ["orchestration", "ai-agents", "state-machine", "claude", "workflow"]`
  - `classifiers = [...]` covering Python versions, dev status (Alpha), intended audience, topic
- [ ] `[project.urls]` includes `Homepage`, `Repository`, `Issues`, `Documentation` (if a docs site exists — fallback to the GitHub README).
- [ ] A `LICENSE` file exists at the repo root matching the chosen license.
- [ ] `RELEASING.md` documents the release procedure:
  ```
  1. Bump version in pyproject.toml
  2. uv build
  3. scripts/verify_wheel_ui.sh dist/*.whl
  4. uv publish --publish-url https://test.pypi.org/legacy/ dist/*
  5. Manual smoke test: install from TestPyPI in a throwaway venv
  6. uv publish dist/*
  7. git tag v0.1.0 && git push --tags
  ```
- [ ] A successful dry-run upload to TestPyPI is completed, and the resulting wheel is installed into a throwaway venv with `uv venv /tmp/fs-tp && /tmp/fs-tp/bin/pip install -i https://test.pypi.org/simple/ flowstate==0.1.0`. Install succeeds, `flowstate --version` prints `0.1.0`.
- [ ] **Do not** publish to production PyPI as part of this issue — only TestPyPI. Production publish is a manual act that happens once the user (Théophane) approves.

## Technical Design

### Files to Create/Modify
- `pyproject.toml` — metadata fill-in.
- `LICENSE` — new (assuming MIT; confirm first).
- `RELEASING.md` — new.
- `scripts/verify_wheel_ui.sh` — referenced, already created in SHARED-008.

### Key Implementation Details
Suggested classifier set:
```
Development Status :: 3 - Alpha
Environment :: Console
Environment :: Web Environment
Intended Audience :: Developers
License :: OSI Approved :: MIT License
Operating System :: MacOS
Operating System :: POSIX :: Linux
Programming Language :: Python :: 3
Programming Language :: Python :: 3.12
Programming Language :: Python :: 3.13
Topic :: Software Development :: Build Tools
Topic :: Software Development :: Libraries :: Python Modules
```

Version management for v0.1: just hardcode `version = "0.1.0"` in `pyproject.toml`. We don't need dynamic versioning for the first release.

### Edge Cases
- License choice — **confirm with the user before writing LICENSE.** The implementing agent must ask if this isn't already established. Most likely MIT or Apache-2.0.
- Private `lumon` dep in the `[lumon]` extra — TestPyPI may reject git URLs even in extras. If so, either (a) leave lumon as `lumon @ git+...` and accept that `flowstate[lumon]` can only be installed with `--pre` or from source, or (b) exclude the extra entirely from the TestPyPI dry run. SHARED-009 made lumon optional precisely to unblock this; verify at dry-run time and adjust.
- `uv publish` credentials — the user needs to provide them manually; document in `RELEASING.md`, do not commit tokens.

## Testing Strategy
Not applicable — this is release pipeline validation. The test is the successful TestPyPI upload and clean reinstall.

## E2E Verification Plan

### Verification Steps
1. `rm -rf dist && uv build` → produces `dist/flowstate-0.1.0-py3-none-any.whl` and `dist/flowstate-0.1.0.tar.gz`.
2. `scripts/verify_wheel_ui.sh dist/flowstate-0.1.0-py3-none-any.whl` → exit 0.
3. `uv publish --publish-url https://test.pypi.org/legacy/ dist/*` → succeeds.
4. `uv venv /tmp/fs-testpypi && /tmp/fs-testpypi/bin/pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ flowstate==0.1.0` → installs.
5. `/tmp/fs-testpypi/bin/flowstate --version` → `0.1.0`.
6. `cd /tmp && mkdir fs-testpypi-project && cd fs-testpypi-project && /tmp/fs-testpypi/bin/flowstate init && /tmp/fs-testpypi/bin/flowstate check flows/example.flow` → PASS.

## E2E Verification Log
_Filled in by the implementing agent._

## Completion Checklist
- [ ] `pyproject.toml` metadata complete
- [ ] `LICENSE` file present (after user confirmation)
- [ ] `RELEASING.md` written
- [ ] TestPyPI dry-run succeeded
- [ ] Fresh venv install succeeded
- [ ] Production PyPI publish is **NOT** done automatically
