# Sprint Phase 31.3 — Packaging & Distribution

**Issues**: SHARED-008, SHARED-009, SERVER-032, SHARED-010, SHARED-011
**Domains**: shared (build + release), server (UI asset resolution), docs
**Date**: 2026-04-11
**Phase**: 31.3 (follows Phase 31.1 project-rooted runtime + Phase 31.2 bootstrap UX, both done + evaluator-PASSED)

## Goal

Turn the functional Phase 31.1/31.2 codebase into a shippable wheel. End state: a user on any machine can `uv tool install flowstate` (from TestPyPI or a local wheel), scaffold a project with `flowstate init`, start the server in a non-repo directory, and load the packaged UI in a browser — with zero Node toolchain, zero git credentials, and zero CWD dependencies.

## Execution Waves

```
Wave 1 (parallel):   SHARED-008 (build hook)    SHARED-009 (lumon extra)
                          │                           │
Wave 2:              SERVER-032 (resolve UI via importlib.resources)
                          │                           │
                          └─────────┬─────────────────┘
Wave 3:                    SHARED-010 (release metadata + TestPyPI dry-run)
                                      │
Wave 4:                    SHARED-011 (README Quickstart + specs.md back-link)
```

SHARED-008 and SHARED-009 touch disjoint surfaces (build tooling vs runtime imports + `pyproject.toml` `[project.dependencies]`); they can be implemented by two agents in parallel. The only shared file is `pyproject.toml`, so coordinate on `[project]` edits — SHARED-008 only touches `[tool.hatch.*]`, SHARED-009 only touches `[project.dependencies]` and `[project.optional-dependencies]`.

## Acceptance Tests

All tests below are runnable by the evaluator. Tests prefixed **[WORKTREE]** run inside this repo on a source checkout. Tests prefixed **[WHEEL]** run against a wheel installed into a throwaway venv. Tests prefixed **[TESTPYPI]** run only after SHARED-010's TestPyPI upload.

---

### SHARED-008: Hatchling UI build hook

**TEST-8.1: `uv build` produces a wheel with packaged UI assets** [WORKTREE]
  Given: A clean worktree with `ui/` present and `src/flowstate/_ui_dist/` empty (only `.gitkeep`)
  When: Running `rm -rf dist && uv build`
  Then: `dist/flowstate-0.1.0-py3-none-any.whl` exists AND `unzip -l dist/flowstate-0.1.0-py3-none-any.whl | grep -E 'flowstate/_ui_dist/(index\.html|assets/)'` returns at least `index.html` and one asset

**TEST-8.2: Wheel UI smoke script passes** [WORKTREE]
  Given: A freshly built `dist/flowstate-0.1.0-py3-none-any.whl`
  When: Running `scripts/verify_wheel_ui.sh dist/flowstate-0.1.0-py3-none-any.whl`
  Then: Exit code is 0 AND stdout confirms `flowstate/_ui_dist/index.html` is present inside the archive

**TEST-8.3: sdist includes the `ui/` source tree** [WORKTREE]
  Given: A freshly built `dist/flowstate-0.1.0.tar.gz`
  When: Running `tar -tzf dist/flowstate-0.1.0.tar.gz | grep -E '^flowstate-0.1.0/ui/(package\.json|src/)'`
  Then: Output includes `ui/package.json` and at least one file under `ui/src/` (so an sdist build can re-run the hook)

**TEST-8.4: `src/flowstate/_ui_dist/` is gitignored but the directory exists** [WORKTREE]
  Given: A clean checkout
  When: Running `git check-ignore -v src/flowstate/_ui_dist/index.html` and `ls src/flowstate/_ui_dist/.gitkeep`
  Then: `git check-ignore` reports the path is ignored AND `.gitkeep` is tracked (so the package directory exists even in a fresh clone)

**TEST-8.5: Node-missing degradation (orchestrator decision)**
  Given: The risk that a dev machine may lack Node 20+
  Then: The contract mandates **fail loudly**. The build hook MUST raise a clear `RuntimeError` (or equivalent Hatchling build failure) with a message like `"npm not found; install Node 20+ to build a release wheel"` when `npm` is missing. Rationale: a silently UI-less wheel would pass TEST-8.2 in neither state cleanly and would ship broken binaries to PyPI. Dev contributors who only touch Python should build with `uv pip install -e .` (no wheel) or set an explicit `FLOWSTATE_SKIP_UI_BUILD=1` env var to skip. If `FLOWSTATE_SKIP_UI_BUILD=1` is set, the hook MUST log a warning and copy nothing — the resulting wheel will fail TEST-8.2 and is explicitly not shippable.

  When: Running `FLOWSTATE_SKIP_UI_BUILD=1 uv build 2>&1`
  Then: Build succeeds, stderr/stdout includes a warning like `"FLOWSTATE_SKIP_UI_BUILD set; wheel will ship without bundled UI"`, AND the produced wheel does NOT contain `_ui_dist/index.html`

---

### SHARED-009: Lumon optional extra

**TEST-9.1: `pyproject.toml` has no git URLs in core dependencies** [WORKTREE]
  Given: The updated `pyproject.toml`
  When: Running `python -c "import tomllib,pathlib; deps=tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['dependencies']; assert not any('git+' in d or '@' in d.split(';')[0].split()[-1] for d in deps), deps"`
  Then: Exit code is 0 (no `git+` URLs, no PEP 508 direct references in `[project.dependencies]`)

**TEST-9.2: `lumon` lives in `[project.optional-dependencies]`** [WORKTREE]
  Given: The updated `pyproject.toml`
  When: Running `python -c "import tomllib,pathlib; ext=tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['optional-dependencies']; assert 'lumon' in ext and any('lumon' in d for d in ext['lumon'])"`
  Then: Exit code is 0 (`[lumon]` extra is declared and contains the lumon package)

**TEST-9.3: Wheel `Requires-Dist` contains zero git URLs** [WORKTREE]
  Given: A freshly built `dist/flowstate-0.1.0-py3-none-any.whl`
  When: Running `unzip -p dist/flowstate-0.1.0-py3-none-any.whl '*.dist-info/METADATA' | grep -i 'Requires-Dist'`
  Then: No line contains `git+` or ` @ ` (direct reference). Lumon appears only under a `Requires-Dist: lumon ...; extra == "lumon"` line OR is omitted entirely if SHARED-010 strips it for TestPyPI.

**TEST-9.4: Core import is lumon-free in a venv without lumon** [WHEEL]
  Given: A throwaway venv with only `pip install dist/flowstate-0.1.0-py3-none-any.whl` (no `[lumon]` extra)
  When: Running `/tmp/fs-wheel-test/bin/python -c "import sys; sys.modules['lumon']=None; from flowstate.cli import app; from flowstate.server.app import create_app; print('OK')"`
  Then: Stdout is `OK` and exit code is 0 (neither `flowstate.cli` nor `flowstate.server.app` transitively imports lumon)

**TEST-9.5: `LUMON_AVAILABLE` reports False without the extra** [WHEEL]
  Given: The same no-extras venv
  When: Running `/tmp/fs-wheel-test/bin/python -c "from flowstate.engine.lumon import LUMON_AVAILABLE; print(LUMON_AVAILABLE)"`
  Then: Stdout is `False`

**TEST-9.6: `require_lumon()` raises a friendly error** [WHEEL]
  Given: The same no-extras venv
  When: Running `/tmp/fs-wheel-test/bin/python -c "from flowstate.engine.lumon import require_lumon; require_lumon()" 2>&1`
  Then: Exit code is non-zero AND stderr contains both `LumonNotInstalledError` and the string `pip install 'flowstate[lumon]'`

**TEST-9.7: Existing tests still pass with lumon installed** [WORKTREE]
  Given: The worktree dev environment (which has lumon)
  When: Running `uv run pytest`
  Then: Test count is equal to or greater than the pre-sprint baseline; lumon-specific tests execute (not skipped); zero failures

**TEST-9.8: Lumon-gated tests skip cleanly when lumon is absent** [WHEEL]
  Given: The no-extras venv with a copy of the test tree
  When: Running `pytest tests/engine/test_lumon_optional.py`
  Then: Passes. Running broader lumon-gated tests shows them as `SKIPPED` with reason containing `"lumon extra not installed"`, not failed or errored

---

### SERVER-032: Serve UI from `importlib.resources`

**TEST-32.1: `locate_ui_dir()` returns the packaged dir when populated** [WORKTREE]
  Given: A source checkout where `src/flowstate/_ui_dist/index.html` exists (simulate by touching a minimal `index.html` in a tmp copy, or by running `cd ui && npm run build && cp -R dist/. ../src/flowstate/_ui_dist/`)
  When: Running `uv run python -c "from flowstate.server.ui_assets import locate_ui_dir; print(locate_ui_dir())"`
  Then: Prints a path ending in `flowstate/_ui_dist` that contains `index.html`

**TEST-32.2: Dev fallback to `ui/dist/` when packaged dir is empty** [WORKTREE]
  Given: A source checkout with `src/flowstate/_ui_dist/` empty (only `.gitkeep`) and `ui/dist/index.html` present
  When: Running `uv run python -c "from flowstate.server.ui_assets import locate_ui_dir; print(locate_ui_dir())"`
  Then: Prints a path ending in `ui/dist` that contains `index.html`

**TEST-32.3: Returns `None` when neither location has an `index.html`** [WORKTREE]
  Given: A source checkout with both `src/flowstate/_ui_dist/` and `ui/dist/` empty/absent
  When: Running `uv run python -c "from flowstate.server.ui_assets import locate_ui_dir; print(locate_ui_dir())"`
  Then: Prints `None` and exit code is 0 (no exception raised)

**TEST-32.4: Server boots with no UI and logs a warning but keeps serving the API** [WORKTREE]
  Given: A valid project + empty `_ui_dist/` and absent `ui/dist/` (move them out of the way temporarily)
  When: Running `uv run flowstate server --port 9095` and hitting `GET /health` and `GET /api/flows`
  Then: Server startup logs contain a warning like `"No built UI found; serving API only"` AT INFO OR WARNING LEVEL; `/health` returns 200; `/api/flows` returns 200; `GET /` returns either 404 or a deterministic placeholder — NOT a 500

**TEST-32.5: Server mount is CWD-independent** [WORKTREE]
  Given: A built UI at `src/flowstate/_ui_dist/` (from running the build hook once)
  When: Running `(cd /tmp && uv run --project $WORKTREE flowstate server --port 9094)` then `curl -sI http://127.0.0.1:9094/`
  Then: First line of headers is `HTTP/1.1 200 OK` (the mount does not depend on CWD containing `ui/dist`)

**TEST-32.6: SPA fallback serves `index.html` for arbitrary non-API paths** [WORKTREE + WHEEL]
  Given: A running server with UI assets resolved (either packaged or dev fallback)
  When: Running `curl -s http://127.0.0.1:<port>/runs/abc123` and `curl -s http://127.0.0.1:<port>/flows`
  Then: Both responses are HTML with `<title>` from the built index (not JSON, not 404)

**TEST-32.7: API-vs-UI routing precedence preserved** [WORKTREE]
  Given: A running server with UI assets resolved
  When: Running `curl -s http://127.0.0.1:<port>/api/flows` and `curl -s http://127.0.0.1:<port>/ws` (the latter as a plain HTTP GET)
  Then: `/api/flows` returns JSON (not HTML); `/ws` returns a WebSocket upgrade error / 404 / method-not-allowed (not the SPA index.html)

---

### SHARED-010: PyPI release metadata + TestPyPI dry-run

**TEST-10.1: `[project]` metadata is complete** [WORKTREE]
  Given: The updated `pyproject.toml`
  When: Running `python -c "import tomllib,pathlib; p=tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']; [p[k] for k in ('name','version','description','readme','license','authors','requires-python','keywords','classifiers','urls')]; print(p['version'])"`
  Then: Exit code is 0, prints `0.1.0`, and all required keys are present. Additionally: `classifiers` contains at least one `Development Status`, one `Programming Language :: Python :: 3.12`, and one `License ::` entry; `urls` contains `Homepage`, `Repository`, and `Issues`

**TEST-10.2: `LICENSE` file exists and matches the declared license** [WORKTREE]
  Given: The repo root
  When: Running `test -f LICENSE && head -3 LICENSE`
  Then: Exit code is 0 AND the file content names the same license declared in `pyproject.toml` (e.g., "MIT License"). The implementing agent MUST confirm the license choice with the user before writing this file — do not guess

**TEST-10.3: `RELEASING.md` documents the release procedure** [WORKTREE]
  Given: The repo root
  When: Running `test -f RELEASING.md` and grepping for the required commands
  Then: `RELEASING.md` exists AND contains all of: `uv build`, `scripts/verify_wheel_ui.sh`, `uv publish`, `test.pypi.org`, and a step that says production PyPI publish is manual

**TEST-10.4: Wheel installs cleanly from a local file into a fresh venv** [WHEEL]
  Given: `dist/flowstate-0.1.0-py3-none-any.whl` built from the worktree
  When: Running `uv venv /tmp/fs-wheel-test && /tmp/fs-wheel-test/bin/pip install /ABS/PATH/dist/flowstate-0.1.0-py3-none-any.whl`
  Then: Install succeeds, exit code 0, no network errors (all deps resolvable from PyPI), no git-URL installation attempts

**TEST-10.5: TestPyPI dry-run upload succeeds** [TESTPYPI]
  Given: Credentials present and TestPyPI reachable
  When: Running `uv publish --publish-url https://test.pypi.org/legacy/ dist/*`
  Then: Upload returns success; the project appears at `https://test.pypi.org/project/flowstate/0.1.0/`
  **Degradation path**: If TestPyPI is down OR credentials are unavailable at the time of evaluation, this test degrades to TEST-10.4 (clean install from a local wheel file path). The sprint contract treats the degraded verification as PASS only if TEST-10.4 passes AND `RELEASING.md` clearly instructs the human operator how to complete the TestPyPI step manually. The evaluator MUST document which variant ran in its verdict.

**TEST-10.6: TestPyPI-installed wheel round-trip works** [TESTPYPI]
  Given: A successful TestPyPI upload
  When: Running `uv venv /tmp/fs-testpypi && /tmp/fs-testpypi/bin/pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ flowstate==0.1.0 && /tmp/fs-testpypi/bin/flowstate --version`
  Then: Install succeeds AND `flowstate --version` prints exactly `0.1.0` (not `0.0.0+dev`)
  **Degradation path**: Same as TEST-10.5 — if TestPyPI is unreachable, this is satisfied by running the equivalent commands against the local wheel installed in TEST-10.4.

**TEST-10.7: Production PyPI is NOT published** [WORKTREE + TESTPYPI]
  Given: Phase 31.3 is complete
  When: Running `pip index versions flowstate 2>&1 | grep -v test` (or equivalent)
  Then: Production `pypi.org` does NOT list a `flowstate` package at version `0.1.0` published by this sprint. (Zero-positive-check: production publish is explicitly out of scope. This test exists to catch accidents.)

---

### SHARED-011: Deployment docs

**TEST-11.1: README has an "Install" section with `uv tool install flowstate`** [WORKTREE]
  Given: The updated `README.md`
  When: Running `grep -E '^## Install|uv tool install flowstate|pipx install flowstate' README.md`
  Then: All three patterns match (section heading + both install commands)

**TEST-11.2: README has a "Quickstart" section with the full first-run sequence** [WORKTREE]
  Given: The updated `README.md`
  When: Running `grep -E 'flowstate init|flowstate check flows/example\.flow|flowstate server' README.md`
  Then: All three commands appear, in order, inside a single "Quickstart" section

**TEST-11.3: README documents `~/.flowstate/projects/<slug>/` isolation** [WORKTREE]
  When: Running `grep -E '~/\.flowstate/projects' README.md`
  Then: At least one match, in a sentence explaining that the project directory is not modified beyond `flowstate init` output

**TEST-11.4: README mentions the `[lumon]` extra** [WORKTREE]
  When: Running `grep -E "flowstate\[lumon\]" README.md`
  Then: At least one match

**TEST-11.5: README links to specs.md §13, and §13.4 back-links to the README Quickstart** [WORKTREE]
  Given: Updated `README.md` and `specs.md`
  When: Inspecting the two files
  Then: `README.md` contains a relative link to `specs.md` anchoring at `#13` (or `#13-...`); `specs.md` §13.4 contains a relative link back to `README.md#quickstart` (or equivalent anchor); both anchors resolve on GitHub's rendered markdown preview

**TEST-11.6: README version references match the pinned release** [WORKTREE]
  Given: `pyproject.toml` version is `0.1.0`
  When: `README.md` quotes a version
  Then: Any version string in the README install instructions is either `0.1.0` or an unpinned form (`flowstate`) — never a stale or mismatched number

---

### Integration: The canonical shippable v0.1 demo

**TEST-INT-1: Full wheel → init → server → HTTP journey** [WHEEL]

This is the single test that proves Phase 31.3 shipped. It must run end-to-end against an installed wheel with no reference to the source checkout.

  Given: A built wheel at `$WORKTREE/dist/flowstate-0.1.0-py3-none-any.whl` (produced by Wave 1 + 2) and no running flowstate server
  When: Running this exact sequence:
```bash
set -euxo pipefail
rm -rf /tmp/fs-wheel-test /tmp/fs-wheel-project
uv venv /tmp/fs-wheel-test
/tmp/fs-wheel-test/bin/pip install $WORKTREE/dist/flowstate-0.1.0-py3-none-any.whl
/tmp/fs-wheel-test/bin/flowstate --version
mkdir -p /tmp/fs-wheel-project && cd /tmp/fs-wheel-project && echo '{}' > package.json
/tmp/fs-wheel-test/bin/flowstate init
/tmp/fs-wheel-test/bin/flowstate check flows/example.flow
/tmp/fs-wheel-test/bin/flowstate server --port 9096 &
SERVER_PID=$!
sleep 2
curl -sf http://127.0.0.1:9096/health
curl -sI http://127.0.0.1:9096/ | head -1
curl -s http://127.0.0.1:9096/ | grep -q '<title'
kill $SERVER_PID
```
  Then: Every command exits 0. Specifically:
  - `flowstate --version` prints `0.1.0` (NOT `0.0.0+dev`)
  - `flowstate init` creates `flowstate.toml` and `flows/example.flow` in the scratch directory
  - `flowstate check` reports the example flow is valid
  - `flowstate server` starts without errors and without any `"ui/dist not found"` warning
  - `GET /health` returns 200 with a JSON body whose `project_slug` corresponds to `/tmp/fs-wheel-project` (matching the Phase 31.1/31.2 contract)
  - `HEAD /` returns `HTTP/1.1 200 OK`
  - `GET /` body contains an HTML `<title>` element (the built UI, not a placeholder, not a 404 page)
  - `~/.flowstate/projects/<slug>/` exists and contains the project's runtime files after the server starts

**TEST-INT-2: `flowstate --version` reads from `importlib.metadata` in the wheel** [WHEEL]
  Given: The same installed wheel
  When: Running `/tmp/fs-wheel-test/bin/python -c "from importlib.metadata import version; print(version('flowstate'))"`
  Then: Prints `0.1.0` and `flowstate --version` CLI output matches byte-for-byte

**TEST-INT-3: Source-checkout fallback for `--version` still works** [WORKTREE]
  Given: The source checkout where `flowstate` has NOT been `pip install -e .`'d (so `importlib.metadata.version("flowstate")` raises `PackageNotFoundError`)
  When: Running `uv run flowstate --version`
  Then: Does not crash; prints a dev-mode string such as `0.0.0+dev` or `0.1.0+dev` — whatever Phase 31.2 chose. The evaluator must confirm this degradation path matches Phase 31.2's existing behavior and was not broken by 31.3.

## Out of Scope

Everything below is explicitly NOT part of Phase 31.3. Surfacing an issue in any of these areas during implementation means: file a follow-up issue, do not widen this sprint.

- Production PyPI publish (`pypi.org`) — TestPyPI dry-run only
- Docker image, Homebrew formula, deb/rpm packages, systemd unit files
- Auth, HTTPS, TLS, multi-tenancy
- Publishing `lumon` itself to any package index — it remains a private git URL inside the `[lumon]` extra
- UI UX/styling changes — the UI only needs to load at `GET /`
- Version bumping automation, changelog generation, release-notes CI
- Windows support (macOS + Linux only for v0.1)
- Flowstate core behavior changes — 31.3 is pure packaging work; if a runtime bug is discovered, file it and do not fix it in this sprint unless it blocks TEST-INT-1
- `/health` endpoint enrichment — the Phase 31.2 contract is frozen; only SERVER-032 may touch server startup, and only to swap the UI asset resolver

## Integration Contract Across Issues

Because multiple issues share `pyproject.toml`, coordinate edits as follows:

1. **SHARED-008 owns**: `[tool.hatch.build.targets.wheel]`, `[tool.hatch.build.targets.sdist]`, `[tool.hatch.build.hooks.custom]`, `.gitignore` (for `_ui_dist`), `hatch_build.py`, `src/flowstate/_ui_dist/.gitkeep`, `scripts/verify_wheel_ui.sh`.
2. **SHARED-009 owns**: `[project.dependencies]` (remove lumon git URL), `[project.optional-dependencies]` (add `lumon`), `src/flowstate/engine/lumon.py` guards, `LumonNotInstalledError`, `tests/engine/test_lumon_optional.py`.
3. **SERVER-032 owns**: `src/flowstate/server/ui_assets.py` (new), the UI mount in `src/flowstate/server/app.py`, `tests/server/test_ui_serving.py`. MUST consume the packaged directory created by SHARED-008 via `importlib.resources.files("flowstate") / "_ui_dist"`. MUST fall back to `ui/dist/` for dev checkouts per TEST-32.2.
4. **SHARED-010 owns**: `[project]` metadata fields (`description`, `readme`, `license`, `authors`, `requires-python`, `keywords`, `classifiers`, `urls`), `version = "0.1.0"`, `LICENSE`, `RELEASING.md`, TestPyPI upload. MUST NOT touch build-hook or dependencies config — those belong to 008/009.
5. **SHARED-011 owns**: `README.md` Install + Quickstart sections, `specs.md §13.4` back-link. MUST NOT regress anything; docs-only.

Artifacts flowing between issues:
- SHARED-008 produces `src/flowstate/_ui_dist/` and `scripts/verify_wheel_ui.sh` — consumed by SERVER-032 (runtime) and SHARED-010 (release pipeline).
- SHARED-009 produces a lumon-free `[project.dependencies]` — consumed by SHARED-010 (so TestPyPI accepts the upload with no direct-URL rejection).
- SERVER-032 produces a CWD-independent UI mount — consumed by TEST-INT-1 which runs outside the worktree.
- SHARED-010 pins `version = "0.1.0"` — consumed by SHARED-011's README version reference.

## Risks Called Out

1. **Node missing on dev machines**. Decision: fail loudly (TEST-8.5). A silently UI-less wheel would poison TestPyPI. An explicit `FLOWSTATE_SKIP_UI_BUILD=1` escape hatch exists for Python-only contributors who never build release wheels; the wheel they produce is explicitly not shippable and does not pass TEST-8.2.
2. **Empty-neither-location fallback**. TEST-32.3 + TEST-32.4 cover the case where both `_ui_dist/` and `ui/dist/` are empty (fresh clone, no UI build). The server must start, log a warning matching the Phase 31.2 INFO-level signature, and keep serving the API. It MUST NOT crash. TEST-INT-1 implicitly covers the happy path.
3. **`importlib.metadata.version("flowstate")` in source checkouts**. Phase 31.2 already handles `PackageNotFoundError` with a dev fallback. TEST-INT-3 explicitly guards against 31.3 breaking that path. The sprint contract acknowledges this is a pre-existing behavior that must be preserved, not a new requirement.
4. **Lumon test guards**. TEST-9.7 runs in the full dev environment (lumon installed) and must match or exceed the pre-sprint test count — prove that the guards did not silently skip existing tests. TEST-9.8 runs in a venv without lumon and confirms gated tests SKIP rather than ERROR.
5. **TestPyPI availability**. TEST-10.5 and TEST-10.6 have documented degradation paths. If TestPyPI is unreachable at evaluation time, the evaluator must fall back to the local-wheel install (TEST-10.4) and note in the verdict which variant ran. This prevents an external outage from blocking the sprint.
6. **License choice**. SHARED-010 explicitly blocks on user confirmation for the license choice. The implementing agent MUST ask before writing `LICENSE`. If the user is unavailable, escalate to the orchestrator.

## Done Criteria

This sprint is complete when all of the following are true:

1. All five issues (SHARED-008, SHARED-009, SERVER-032, SHARED-010, SHARED-011) are marked `done` in their issue files and in `issues/PLAN.md`.
2. `uv run pytest` in the worktree passes with no regressions vs the pre-sprint baseline (TEST-9.7).
3. `uv run ruff check .` and `uv run pyright` pass with no new errors.
4. `cd ui && npm run lint` passes.
5. Every `[WORKTREE]` and `[WHEEL]` test above has a PASS verdict from the evaluator.
6. `[TESTPYPI]` tests either PASS or the evaluator has explicitly documented the degradation (TestPyPI unreachable → TEST-10.4 local-wheel install substitutes).
7. **TEST-INT-1 (the canonical shippable v0.1 demo) passes end-to-end against the installed wheel** — this is the blocking gate. If TEST-INT-1 fails, the sprint does not ship, regardless of the state of other tests.
8. No production PyPI upload has occurred (TEST-10.7).
9. The orchestrator has committed each issue in a separate commit following the `[ISSUE-ID] ...` convention.
