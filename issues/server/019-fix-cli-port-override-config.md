# [SERVER-019] Fix CLI port override not propagating to config

## Domain
server

## Status
done

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: —

## Spec References
- specs.md Section 9.8 — Server configuration

## Summary
The CLI `--port` flag overrides the port for uvicorn but does not update `config.server_port`. The executor constructs `server_base_url` from the config object, so subtask management instructions inject the default port (8080) instead of the actual port (9090). Agents call the wrong port and subtasks never persist. Fixed by mutating `cfg.server_host`/`cfg.server_port` before creating the app, and changing the default port to 9090.

## Acceptance Criteria
- [x] CLI `--port` flag updates `cfg.server_port` before app creation
- [x] CLI `--host` flag updates `cfg.server_host` before app creation
- [x] Default port changed from 8080 to 9090
- [x] Subtask API instructions inject the correct port

## Technical Design

### Files to Create/Modify
- `src/flowstate/cli.py` — mutate config instead of using local variables
- `src/flowstate/config.py` — change default port to 9090

### Key Implementation Details
Already implemented inline.

## Testing Strategy
- Start server with `--port 9090`, run a flow with `subtasks=true`, verify subtasks persist

## Completion Checklist
- [x] `/lint` passes
- [x] Acceptance criteria verified
