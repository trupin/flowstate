# [ENGINE-047] judge=false not respected — judge still invoked for conditional edges

## Domain
engine

## Status
done

## Priority
P2

## Summary
In `e2e_self_report.flow` with `judge = false`, the executor still invokes a judge subprocess and logs "Judge decided" activity messages. The self-report routing mechanism (DECISION.json) should be used instead.

## Evidence
- Flow: `e2e_self_report.flow` has `judge = false` at flow level
- Activity logs contain "Judge decided" messages
- Flow completed successfully (routing worked), but via judge not self-report

## Acceptance Criteria
- [ ] When `judge = false`, the executor uses self-report routing (reads DECISION.json)
- [ ] No "Judge decided" activity logs when judge is disabled
- [ ] E2E self-report suite passes
