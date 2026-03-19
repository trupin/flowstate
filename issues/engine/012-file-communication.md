# [ENGINE-012] File Communication Protocol

## Domain
engine

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: ENGINE-013, ENGINE-015, ENGINE-016

## Spec References
- specs.md Section 9 — "Claude Code Integration"
- specs.md Section 2.6.1 — "Flowstate Data Directory"

## Summary
Define standard file formats for inter-agent communication: `INPUT.md` (task context written before launch), `REQUEST.md` (judge evaluation request), and `DECISION.json` (judge decision output). Add file I/O functions to `context.py` and `judge.py` so the orchestrator and engine can communicate via the filesystem. This enables the orchestrator agent to read task context from files and write decisions back, rather than receiving everything via prompt injection.

## Acceptance Criteria
- [ ] `write_task_input(task_dir, prompt) -> str` writes `INPUT.md` to the task directory and returns its path
- [ ] `INPUT.md` contains the full assembled prompt (same content currently passed to `claude -p`)
- [ ] `write_judge_request(judge_dir, context: JudgeContext) -> str` writes `REQUEST.md` and returns its path
- [ ] `REQUEST.md` contains: node name, task prompt, exit code, summary, cwd, available transitions
- [ ] `write_judge_decision(judge_dir, decision, reasoning, confidence) -> str` writes `DECISION.json` and returns its path
- [ ] `DECISION.json` contains: `{ "decision": str, "reasoning": str, "confidence": float }`
- [ ] `read_judge_decision(judge_dir) -> JudgeDecision` reads and parses `DECISION.json`
- [ ] `create_judge_dir(run_data_dir, source_node, generation) -> str` creates judge directory
- [ ] All functions handle missing files gracefully (raise typed exceptions or return None)
- [ ] All tests pass

## Technical Design

### Files to Create/Modify
- `src/flowstate/engine/context.py` — Add `write_task_input()`, `create_judge_dir()`
- `src/flowstate/engine/judge.py` — Add `write_judge_request()`, `read_judge_decision()`, `write_judge_decision()`
- `tests/engine/test_file_protocol.py` — Tests for all file I/O functions

### Key Implementation Details

#### Directory Structure

```
~/.flowstate/runs/<run-id>/
├── tasks/
│   ├── <node>-<gen>/
│   │   ├── INPUT.md         # NEW: Full assembled prompt
│   │   ├── SUMMARY.md       # Existing: Task output
│   │   └── artifacts/       # Existing: scratch files
├── judge/
│   ├── <source>-<gen>/
│   │   ├── REQUEST.md       # NEW: Judge evaluation request
│   │   └── DECISION.json    # NEW: Judge decision output
└── orchestrator/             # Created by ENGINE-014
```

#### write_task_input (context.py)

```python
def write_task_input(task_dir: str, prompt: str) -> str:
    """Write the assembled task prompt to INPUT.md in the task directory."""
    input_path = Path(task_dir) / "INPUT.md"
    input_path.write_text(prompt)
    return str(input_path)
```

#### Judge File I/O (judge.py)

```python
def create_judge_dir(run_data_dir: str, source_node: str, generation: int) -> str:
    """Create judge directory: <run_data_dir>/judge/<source>-<gen>/"""
    judge_dir = Path(run_data_dir) / "judge" / f"{source_node}-{generation}"
    judge_dir.mkdir(parents=True, exist_ok=True)
    return str(judge_dir)

def write_judge_request(judge_dir: str, context: JudgeContext) -> str:
    """Write REQUEST.md with judge evaluation context."""
    # Format: same content as build_judge_prompt() but in file form

def write_judge_decision(judge_dir: str, decision: str, reasoning: str, confidence: float) -> str:
    """Write DECISION.json with structured judge decision."""
    # {"decision": "target_name", "reasoning": "...", "confidence": 0.85}

def read_judge_decision(judge_dir: str) -> JudgeDecision:
    """Read and parse DECISION.json from judge directory."""
    # Raises FileNotFoundError if missing, ValueError if malformed
```

### Edge Cases
- `INPUT.md` is written but task crashes before reading it — safe, file persists for debugging
- `DECISION.json` doesn't exist yet when engine tries to read — raise clear error
- `DECISION.json` has invalid JSON — raise ValueError with context
- Judge directory already exists (retry scenario) — `exist_ok=True` handles it

## Testing Strategy
1. **test_write_task_input** — Write prompt, verify file exists and contents match
2. **test_create_judge_dir** — Create dir, verify path structure
3. **test_write_judge_request** — Write request, verify all fields present
4. **test_write_read_judge_decision** — Write then read, verify round-trip
5. **test_read_judge_decision_missing** — No file, verify FileNotFoundError
6. **test_read_judge_decision_malformed** — Bad JSON, verify ValueError
