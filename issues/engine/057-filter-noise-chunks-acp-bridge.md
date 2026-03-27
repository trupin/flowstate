# [ENGINE-057] Filter noise streaming chunks at ACP bridge before storage

## Domain
engine

## Status
done

## Priority
P1 (important)

## Dependencies
- Depends on: none
- Blocks: none

## Summary
The ACP bridge (`acp_client.py`) passes every streaming chunk from the Claude API through to the database — including empty strings, single-character fragments (`.`, `,`), and whitespace-only chunks. These create hundreds of useless log entries per task. The bridge should filter noise at the source before it reaches the executor or database, not rely on the UI to hide them at render time.

## Acceptance Criteria
- [ ] Empty text chunks (empty string or whitespace-only) are not emitted as StreamEvents
- [ ] Single-character non-alphanumeric fragments (`.`, `,`, `:`, etc.) are not emitted
- [ ] Multi-character meaningful content is still emitted normally
- [ ] Tool call and tool result events are not affected by this filter
- [ ] System events are not affected
- [ ] Existing tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/acp_client.py` — add noise filter before StreamEvent creation

### Key Implementation Details

Add a helper function and guard it in the AgentMessageChunk and AgentThoughtChunk handlers:

```python
def _is_noise_chunk(text: str) -> bool:
    """Filter streaming fragments that add no informational value."""
    trimmed = text.strip()
    if not trimmed:
        return True
    if len(trimmed) == 1 and not trimmed.isalnum():
        return True
    return False
```

In `_acp_update_to_stream_event()`, for `AgentMessageChunk` and `AgentThoughtChunk` cases, return `None` if `_is_noise_chunk(text)` is true. The caller already handles `None` returns (skips them).

### Edge Cases
- Multi-character punctuation like `...` or `---`: NOT filtered (these could be meaningful)
- Single letter or digit: NOT filtered (could be start of a word in streaming)
- Tool results containing single characters: NOT affected (filter only applies to assistant/thinking chunks)

## Testing Strategy
- Unit test: verify noise chunks are filtered out
- Unit test: verify meaningful chunks pass through
- Existing ACP harness tests still pass

## E2E Verification Plan
### Verification Steps
1. Start server, run a flow with subtasks
2. Check task logs via API
3. Expected: no empty or single-char assistant/thinking entries in the logs

## E2E Verification Log

### Post-Implementation Verification

**Server startup:**
```
$ uv run flowstate server --port 9090
Starting Flowstate server on 127.0.0.1:9090
INFO:     Started server process [35149]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:9090 (Press CTRL+C to quit)
```

**Step 1: Query existing runs to find completed flows:**
```
$ curl -s http://localhost:9090/api/runs
```
Result: 27 runs found. Selected completed run `6e0b5e3d-b5f7-44f5-bb43-8061fb15f2dc` (flow: `discuss_flowstate`, status: `completed`).

**Step 2: Get run details and identify tasks:**
```
$ curl -s http://localhost:9090/api/runs/6e0b5e3d-b5f7-44f5-bb43-8061fb15f2dc
```
Result: 8 completed tasks found (moderator x3, alice x2, bob x2, done x1). All use the `claude` harness (SubprocessManager), not the ACP harness.

**Step 3: Check all available flows for harness type:**
```
$ curl -s http://localhost:9090/api/flows
```
Result: All 3 registered flows (`agent_delegation`, `discuss_flowstate`, `implement_flowstate`) use `harness=claude`. No ACP-harness flows are configured on this system.

**Step 4: Analyze existing logs for noise patterns (to confirm the problem exists in the claude harness path and that the ACP filter would address it for ACP clients):**
```
$ curl -s http://localhost:9090/api/runs/6e0b5e3d-b5f7-44f5-bb43-8061fb15f2dc/tasks/c6fa640d-ee7a-47c8-8cc4-6d02358d54f2/logs
```
Moderator task (c6fa640d): 81 log entries, 9 noise entries found (empty strings and single-period assistant/thinking chunks).

```
$ curl -s http://localhost:9090/api/runs/6e0b5e3d-b5f7-44f5-bb43-8061fb15f2dc/tasks/e24a47d2-e63d-4ed4-9567-4517175d7c31/logs
```
Alice task (e24a47d2): 106 log entries, 8 noise entries found (empty strings and single-period thinking/assistant chunks).

This confirms the noise pattern (empty text `""` and single-punctuation `.`) exists in production logs from the claude harness, validating the problem statement.

**Step 5: Unit test verification (noise filter in ACP bridge):**
```
$ uv run pytest tests/engine/test_acp_client.py -v -k "noise"
28 passed, 79 deselected in 0.13s
```
All 28 noise-filter tests pass, covering:
- `TestIsNoiseChunk` (15 tests): empty, whitespace, single punctuation (`.`, `,`, `:`, `;`, `-`), whitespace-padded punctuation, single letters/digits pass through, multi-char punctuation passes, words/sentences/code pass.
- `TestNoiseFilterInEventMapping` (13 tests): `AgentMessageChunk` and `AgentThoughtChunk` with noise return `None`, meaningful content passes, tool call events unaffected, plan events unaffected.

**Step 6: Full engine test suite (regression check):**
```
$ uv run pytest tests/engine/ -q
545 passed in 31.55s
```

**Step 7: Lint and type checks:**
```
$ uv run ruff check src/flowstate/engine/acp_client.py tests/engine/test_acp_client.py
All checks passed!

$ uv run pyright src/flowstate/engine/acp_client.py
0 errors, 0 warnings, 0 informations
```

**Conclusion:**
The noise filter is correctly implemented in `_is_noise_chunk()` and integrated into `_acp_update_to_stream_event()` for both `AgentMessageChunk` and `AgentThoughtChunk` events. The filter cannot be exercised E2E against the real running server because all configured flows use the `claude` harness (SubprocessManager), not the ACP harness. Running an ACP-harness flow would require an external ACP-compatible agent (with a valid ANTHROPIC_API_KEY for API-based agents). The 28 dedicated unit tests comprehensively cover all edge cases including the exact noise patterns observed in production logs (empty strings and single-period chunks). The full engine suite (545 tests) passes with zero regressions.

## Completion Checklist
- [ ] Unit tests written and passing
- [ ] `/simplify` run on all changed code
- [ ] `/lint` passes (ruff, pyright, eslint)
- [ ] Acceptance criteria verified
- [ ] E2E verification log filled in with concrete evidence
