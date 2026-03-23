# [SHARED-004] Make input/output blocks mandatory, remove hardcoded title/description

## Domain
shared

## Status
todo

## Priority
P0 (critical path)

## Dependencies
- Depends on: —
- Blocks: —

## Summary
The `input { ... }` and `output { ... }` blocks should be mandatory in the DSL — every flow must declare what inputs it expects and what outputs it produces. Currently, flows without these blocks still accept tasks, and the task modal always shows hardcoded "Title" and "Description" fields regardless of the DSL declaration. The queue manager also injects `title` and `description` into params automatically. All of this should be driven solely by the flow's declared input fields.

## Changes Required

### 1. DSL Type Checker — make input mandatory
**File:** `src/flowstate/dsl/type_checker.py`
- Add rule: flow must have at least one input field (empty `input {}` is OK but the block must exist)
- Output block can be optional (not all flows produce structured output)

### 2. Queue Manager — use declared input fields, not hardcoded title/description
**File:** `src/flowstate/engine/queue_manager.py`
- Remove hardcoded `task_params["title"] = task.title`
- Instead, map task fields to flow input fields: `task.params_json` already contains the user-provided values matching the flow's declared input fields
- The task's `title` and `description` are metadata for the queue UI, NOT flow input params

### 3. Task Modal — render fields from flow's input declaration
**File:** `ui/src/components/TaskModal/TaskModal.tsx`
- Remove hardcoded Title and Description inputs
- Instead, dynamically render input fields based on the flow's `input_fields` (from `flow.params` or `ast_json.input_fields`)
- Each field: label = field name, type = string/number/bool, default = field default
- Required fields (no default) marked with *

### 4. Submit Task API — validate against input fields
**File:** `src/flowstate/server/routes.py`
- In `submit_task()`, validate that the provided params match the flow's declared input fields
- Required fields (no default) must be present
- Unknown fields are rejected

### 5. Task table — params_json is the ONLY input data
**File:** `src/flowstate/state/models.py`
- The `title` and `description` fields on `TaskRow` become optional metadata (for display in queue)
- The actual input data is in `params_json` (matches flow's input fields)
- Title for queue display can be auto-generated from the first input field or a flow-level config

## Acceptance Criteria
- [ ] Flows without `input { ... }` fail type checking
- [ ] Task modal renders only the declared input fields (no hardcoded Title/Description)
- [ ] Queue manager passes only declared input field values as params (no injected title/description)
- [ ] Submit task API validates params against declared input fields
- [ ] Existing flows with `input { title: string, description: string }` continue to work

## Testing Strategy
- Type checker test: flow without input block → error
- Task modal: renders fields dynamically from flow declaration
- Queue manager: params match input fields only
- E2E: submit task with correct/incorrect input fields
