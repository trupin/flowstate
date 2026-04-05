# [ENGINE-076] Create flowstate Lumon plugin for artifact submission

## Domain
engine

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: ENGINE-077

## Spec References
- specs.md Section 9.9 — "Lumon Sandboxing"
- specs.md Section 9.6 — "API-Based Artifact Protocol"

## Summary
Build a `flowstate` Lumon plugin that allows sandboxed agents to submit artifacts (summaries, decisions, output) through Lumon's type-safe primitives instead of raw curl commands. The plugin ships with Flowstate and is auto-included in every Lumon-enabled task. Agents call `flowstate.submit_summary(...)`, `flowstate.submit_decision(...)`, etc. — the plugin's Python implementation POSTs to the Flowstate API under the hood.

## Acceptance Criteria
- [ ] Plugin directory exists at `src/flowstate/engine/lumon_plugin/`
- [ ] `manifest.lumon` defines: `flowstate.guide`, `flowstate.submit_summary`, `flowstate.submit_decision`, `flowstate.submit_output`
- [ ] `impl.lumon` implements the functions using Lumon's `io` or shell primitives to call the Python backend
- [ ] Python backend (`flowstate_plugin.py`) POSTs to `$FLOWSTATE_SERVER_URL/api/runs/$FLOWSTATE_RUN_ID/tasks/$FLOWSTATE_TASK_ID/artifacts/{name}`
- [ ] `flowstate.guide()` returns usage instructions for the agent
- [ ] `flowstate.submit_summary(text)` POSTs text/markdown summary artifact
- [ ] `flowstate.submit_decision(target, reasoning, confidence)` POSTs JSON decision artifact
- [ ] `flowstate.submit_output(data)` POSTs JSON output artifact
- [ ] Plugin reads env vars `FLOWSTATE_SERVER_URL`, `FLOWSTATE_RUN_ID`, `FLOWSTATE_TASK_ID`
- [ ] Plugin works with Lumon's `--working-dir sandbox` flag
- [ ] Update prompt builders in `context.py`: when Lumon is active, use `flowstate.submit_*` instructions instead of curl
- [ ] Tests verify plugin manifest parses and functions are callable

## Technical Design

### Files to Create

**`src/flowstate/engine/lumon_plugin/manifest.lumon`:**
```lumon
define flowstate.guide
  "Return usage guidelines for the flowstate plugin — read this before submitting artifacts"
  returns: text "Best practices for artifact submission"

define flowstate.submit_summary
  "Submit a task summary to the Flowstate API"
  takes:
    content: text "Markdown summary of what you did, what changed, the outcome"
  returns: :ok(text) | :error(text) "Success message or error"

define flowstate.submit_decision
  "Submit a routing decision to the Flowstate API"
  takes:
    target: text "Target node name for the transition"
    reasoning: text "Brief explanation of why this transition was chosen"
    confidence: number "Confidence score from 0.0 to 1.0"
  returns: :ok(text) | :error(text) "Success message or error"

define flowstate.submit_output
  "Submit structured output for cross-flow filing"
  takes:
    data: text "JSON string of key-value pairs for target flow input"
  returns: :ok(text) | :error(text) "Success message or error"
```

**`src/flowstate/engine/lumon_plugin/impl.lumon`:**
The implementation calls the Python backend script via shell command. Lumon plugins can invoke Python through `io.shell` or similar mechanism — check how existing plugins (e.g., browser) invoke their Python backends in ~/code/lumon-test/plugins/.

**`src/flowstate/engine/lumon_plugin/flowstate_plugin.py`:**
```python
"""Flowstate artifact submission backend for the Lumon plugin."""
import json
import os
import sys
import urllib.request

SERVER_URL = os.environ.get("FLOWSTATE_SERVER_URL", "")
RUN_ID = os.environ.get("FLOWSTATE_RUN_ID", "")
TASK_ID = os.environ.get("FLOWSTATE_TASK_ID", "")

def submit_artifact(name: str, content: str, content_type: str) -> str:
    url = f"{SERVER_URL}/api/runs/{RUN_ID}/tasks/{TASK_ID}/artifacts/{name}"
    data = content.encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req) as resp:
            return f"ok: {resp.status}"
    except Exception as e:
        return f"error: {e}"
```

### Files to Modify

**`src/flowstate/engine/context.py`:**
- Add `build_lumon_artifact_instructions()` function that returns Lumon-specific artifact submission instructions
- Modify `_build_directory_sections()` to check if lumon is active and use Lumon instructions
- Modify `build_routing_instructions()` similarly
- The instructions should reference `flowstate.submit_summary(...)` instead of curl

### How Lumon Plugins Work (from ~/code/lumon-test)

Look at `~/code/lumon-test/plugins/browser/` for the pattern:
- `manifest.lumon` — function signatures (define blocks)
- `impl.lumon` — Lumon implementation that calls Python
- Python files — system-level implementation

The plugin is discovered by Lumon at `../plugins/` relative to the `--working-dir`.

### Edge Cases
- Env vars not set (non-Lumon context): plugin functions return error
- Server not running: plugin returns error, agent sees failure
- Large content: urllib.request handles up to request limits

## Testing Strategy
- Verify manifest.lumon parses with `lumon browse flowstate` from a test directory
- Verify Python backend submits artifacts (mock HTTP server)
- Verify prompt builders produce correct Lumon instructions when lumon=true

## E2E Verification Plan

### Verification Steps
1. Set up a test directory with the plugin at `plugins/flowstate/`
2. Run `lumon --working-dir sandbox browse flowstate` — should show function signatures
3. Run `lumon --working-dir sandbox 'return flowstate.guide()'` — should return instructions

## E2E Verification Log
_[Agent fills this in]_

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/lint` passes
- [ ] Acceptance criteria verified
