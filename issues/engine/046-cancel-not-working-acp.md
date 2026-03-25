# [ENGINE-046] Cancel flow does not stop AcpHarness subprocess

## Domain
engine

## Status
done

## Priority
P1

## Dependencies
- Depends on: ENGINE-045

## Summary
During E2E cancel suite: clicking "Cancel" in the UI does not terminate the running AcpHarness subprocess. The flow stays in "running" status and claude-agent-acp processes remain alive. The cancel button was visually present and clicked, but the run status never changed to "cancelled".

Two possible causes:
1. The cancel button click doesn't reach the cancel API endpoint
2. The `AcpHarness.kill()` method doesn't properly terminate the `claude-agent-acp` process

## Acceptance Criteria
- [ ] Cancel button click triggers the cancel API endpoint
- [ ] AcpHarness.kill() terminates the subprocess
- [ ] Flow transitions to "cancelled" status within 10 seconds
- [ ] No orphan processes after cancellation

## Evidence
- Screenshot: `/tmp/flowstate-e2e-cancel-clicked.png` — Cancel button visible, flow still running
- Post-cancel `pgrep -af claude-agent-acp` shows 2 orphan processes
