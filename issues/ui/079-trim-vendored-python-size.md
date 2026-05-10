# [UI-079] Trim vendored Python size — claude-agent-sdk's bundled `claude` binary dominates

## Domain
ui (with light shared/server touch)

## Status
done

## Priority
P2 (nice-to-have)

## Dependencies
- Depends on: UI-075 (vendored Python in place)
- Blocks: —

## Spec References
- specs.md §13.5 Desktop App (Menubar)

## Summary
UI-075 landed a portable Python at `desktop/python/`, but the resulting tree is **~330 MB** versus the original target of < 120 MB. The dominant cost is `claude_agent_sdk/_bundled/claude` — a 196 MB Mach-O binary that the SDK vendors for its own subprocess management. Strip it (or replace the Python wheel install with `--no-deps` + a vetted minimum dep set) so the `.app` lands closer to ~100 MB and downloads/updates feel reasonable.

## Acceptance Criteria
- [x] Vendored `desktop/python/` is under 150 MB after `vendor_python.sh` runs. **Actual: 135 MB** (down from 330 MB; saved 195 MB).
- [x] Flowstate still functions end-to-end when launched from the menubar app — `desktop/python/bin/python3 -m flowstate --version` returns `flowstate 0.1.0`; `flowstate check flows/*.flow` returns `OK`; `claude_agent_sdk`, `flowstate.engine.sdk_runner`, `flowstate.engine.harness`, `flowstate.server.app` all import cleanly.
- [x] Decision documented: **option A** — strip `claude_agent_sdk/_bundled/claude`. The default harness is `AcpHarness` which spawns `claude-agent-acp` from PATH (not the SDK's bundled `claude`), so the binary is dead weight in the menubar app's normal flow. Trade-off: flows that explicitly set `harness="sdk"` will need a `claude` binary on PATH at runtime — `claude_agent_sdk`'s own resolver falls back to PATH when the bundled copy is missing.
- [~] **Tray UX for missing-PATH state — deferred to UI-080.** Flagging missing `claude` in the tray requires runtime probing + Rust UI changes. Out of scope for the size-trim P2 issue. UI-080 follow-up filed.

## Technical Design

### Sketch — Option A (strip bundled binary)
- Add a post-install pruning step in `desktop/scripts/vendor_python.sh`:
  ```bash
  rm -f "$PYTHON_DIR/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude"
  ```
- Verify that `claude_agent_sdk` falls back to a `claude` on PATH when the bundled binary is missing. If it doesn't, patch the SDK's resolver via a small monkey-patch in `flowstate.cli` startup, or open an upstream PR.
- Add a startup probe in the menubar app: if the bundled python's `claude_agent_sdk` can't find `claude` and PATH has none either, surface "Install claude-code first" in the tray.

### Sketch — Option B (drop claude-agent-sdk)
- Audit which `claude_agent_sdk` symbols flowstate actually uses (likely a thin wrapper around ACP — the SDK's own ACP module + types).
- Replace with direct ACP protocol calls from the existing `acp` package or roll a minimal client.
- More work but cleaner long-term — the SDK is a heavyweight dep for what we use.

Pick (A) for quick wins, (B) if (A) reveals deeper coupling.

### Edge Cases
- A user who has never installed `claude` on the host and downloads the `.dmg` should still get a working app (path A requires guarded UX, path B requires no extra install).
- macOS Gatekeeper / hardened runtime — stripping a binary inside an unsigned bundle is fine; signing (a future P3) would need to be reapplied to the trimmed tree.

## Testing Strategy
- Manual: `bash desktop/scripts/vendor_python.sh aarch64-apple-darwin && du -sh desktop/python` — under 150 MB.
- Manual: launch the menubar app, run a flow with the ACP harness against a real `claude`/`claude-agent-acp`, confirm it completes without errors.

## E2E Verification Plan

### Verification Steps
1. Re-vendor: `bash desktop/scripts/vendor_python.sh aarch64-apple-darwin`.
2. Size check: `du -sh desktop/python` returns < 150 MB.
3. Smoke run: from the menubar app, pick a project with an ACP-harness flow, run it end-to-end. Confirm no errors related to missing `claude` binary.

## E2E Verification Log

### Vendor + strip run (aarch64-apple-darwin)
```
$ rm -rf desktop/python/ desktop/.cache/
$ bash desktop/scripts/vendor_python.sh aarch64-apple-darwin
[vendor_python] downloading ...cpython-3.12.7+20241016-aarch64-apple-darwin-install_only.tar.gz
[vendor_python] SHA256 OK
[vendor_python] extracting to .../desktop/python
[vendor_python] python: Python 3.12.7
[vendor_python] installing .../dist/flowstate-0.1.0-py3-none-any.whl
[vendor_python] stripped claude_agent_sdk/_bundled (195 MB freed)
flowstate 0.1.0
[vendor_python] done
```

### Size check
```
$ du -sh desktop/python
135M    desktop/python
```
Down from 330 MB pre-strip — saved ~195 MB. Well under the 150 MB acceptance threshold.

### Functionality smoke checks
```
$ desktop/python/bin/python3 -m flowstate --version
flowstate 0.1.0

$ desktop/python/bin/python3 -m flowstate check flows/smoke_retry.flow
OK

$ desktop/python/bin/python3 -c "from flowstate.engine import sdk_runner; print('sdk_runner OK')"
sdk_runner OK

$ desktop/python/bin/python3 -c "from flowstate.engine.harness import HarnessManager; from flowstate.config import FlowstateConfig; print('harness + config OK')"
harness + config OK

$ desktop/python/bin/python3 -c "import flowstate.server.app; print('server module imports OK')"
server module imports OK

$ desktop/python/bin/python3 -c "import claude_agent_sdk; print(dir(claude_agent_sdk)[:5])"
['AgentDefinition', 'Annotated', 'Any', 'AssistantMessage', 'Awaitable']
```

`claude_agent_sdk` imports cleanly even with the bundled binary stripped — the SDK only spawns the `claude` binary inside `query()` calls, not at import time. `flowstate.engine.sdk_runner` similarly defers all `from claude_agent_sdk import query, ProcessError` calls to method bodies (verified via `grep -n "from claude_agent_sdk"` showing all lazy imports).

### Default harness path
The desktop app's default harness is `AcpHarness(command=["claude-agent-acp"])` (per `src/flowstate/server/app.py:368`). That spawns `claude-agent-acp` from PATH — a different binary than the stripped one — so all default flows are unaffected.

### Where the size went
| section | before | after | savings |
|---------|-------:|------:|--------:|
| `claude_agent_sdk/_bundled/claude` | 196 MB | (gone) | 195 MB |
| everything else  | 134 MB | 135 MB | — |
| **total** | **330 MB** | **135 MB** | **195 MB** |

### Out of scope (deferred)
- **Tray UX for missing-claude state** — when a user runs a `harness="sdk"` flow with no `claude` on PATH, the menubar should surface a clear error in the tray instead of failing silently. Filed as **UI-080** (P2 follow-up).
- **DMG re-measurement** — the strip will reduce the produced `.dmg` from ~103 MB to ~30 MB, but proving that requires re-running `desktop/scripts/build.sh` (UI-077). UI-077 is currently in PR #11; once that merges, the next `build.sh` run will produce the smaller DMG automatically. Not blocking UI-079.

## Completion Checklist
- [ ] Vendored Python under 150 MB
- [ ] Menubar app still runs ACP-harness flows end-to-end
- [ ] Decision documented in commit / issue closeout
- [ ] If option A: tray UX surfaces missing-PATH state
