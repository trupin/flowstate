---
description: Create a new Flowstate state machine (.flow file) from a natural language description of the workflow
user_invocable: true
---

Create a new `.flow` file from the user's description of what they want to automate.

## 1. Understand the workflow

Ask clarifying questions if needed, but infer reasonable defaults when possible. Determine:

- **What are the steps?** Each distinct unit of work becomes a node.
- **What's the flow topology?** Linear, fork-join (parallel), conditional branching, cycles, or a mix.
- **What workspace should tasks run in?** Usually the current project directory.
- **What parameters does the user need?** Template variables for reusable flows.
- **What's the error policy?** Default to `pause` (safest — lets the user decide).
- **What's the time budget?** Estimate based on complexity.

## 2. Design the graph

Map the workflow to Flowstate's graph model:

| Pattern | When to use | DSL syntax |
|---------|-------------|------------|
| **Linear** (`A -> B -> C`) | Steps must happen in sequence | `A -> B` then `B -> C` |
| **Fork-join** (`A -> [B,C] -> D`) | Steps can run in parallel | `A -> [B, C]` then `[B, C] -> D` |
| **Conditional** (`A -> B when "..."`) | Next step depends on outcome | `A -> B when "condition"` then `A -> C when "other condition"` |
| **Cycle** (`A -> B -> A when "..."`) | Retry/iterate until a condition is met | Back-edge with `when` clause |

Rules to follow:
- Exactly **one `entry`** node (the starting point)
- At least **one `exit`** node (where the flow ends)
- Every node must be **reachable** from entry and must be able to **reach an exit**
- Conditional edges from a node must cover all outgoing edges (no mixing unconditional + conditional)
- `session` context mode is NOT allowed on fork or join edges
- Cycles MUST have at least one conditional edge (to prevent infinite loops)
- Flows with cycles MUST have `budget > 0`

## 3. Write the .flow file

Use this structure:

```
flow <name> {
    budget = <duration>           // e.g., 1h, 30m, 2h
    on_error = pause              // pause | abort | skip
    context = handoff             // handoff | session | none
    workspace = "<path>"          // working directory for tasks

    // Optional parameters
    param <name>: <type>          // string, number, bool
    param <name>: <type> = <default>

    // Nodes
    entry <name> {
        prompt = """
        <what the AI agent should do>
        """
    }

    task <name> {
        prompt = """
        <what the AI agent should do>
        """
    }

    exit <name> {
        prompt = """
        <what the AI agent should do>
        """
    }

    // Edges
    <source> -> <target>                           // unconditional
    <source> -> <target> when "<condition>"         // conditional
    <source> -> [<t1>, <t2>]                       // fork (parallel)
    [<t1>, <t2>] -> <target>                       // join (wait for all)

    // Edge with config (optional)
    <source> -> <target> {
        context = handoff        // override flow default
        delay = 5m               // wait before starting target
    }
}
```

### Prompt writing guidelines

Each prompt tells a Claude Code agent what to do. Write prompts that are:

- **Specific**: Tell the agent exactly what to do, not just a vague goal.
- **Self-contained**: Include enough context that the agent can work without asking questions.
- **Output-oriented**: Say what files to create/modify and what the success criteria are.
- **Workspace-aware**: Reference files relative to the workspace (the agent's cwd).

Good prompt:
```
"""
Read src/api/routes.py and add input validation to all POST endpoints.
Use Pydantic models for request bodies. Add appropriate error responses
(400 for validation errors, 404 for not found). Write tests in
tests/test_routes.py for each validation case.
"""
```

Bad prompt:
```
"Fix the API"
```

### When conditions (for conditional edges)

`when` clauses are evaluated by a judge agent that reads the previous task's SUMMARY.md. Write conditions as clear, evaluable statements:

- Good: `when "all tests pass and coverage is above 80%"`
- Good: `when "the review found issues that need fixing"`
- Bad: `when "done"` (too vague for the judge)

## 4. Validate the flow

After writing the `.flow` file, validate it:

```bash
uv run flowstate check <path_to_flow_file>
```

This runs the parser and all 18 type checker rules (S1-S8, E1-E9, C1-C3, F1-F3). Fix any errors reported.

## 5. Save and report

Save the `.flow` file to the location the user specifies, or to `./flows/<name>.flow` by default (the server's watch directory).

Report:
- The flow topology (number of nodes, edges, any parallel/conditional/cycle patterns)
- How to run it: `uv run flowstate run <path>` or via the web UI
- Any parameters that need to be provided at run time

## Common patterns

### Code review with parallel tests
```
flow code_review {
    budget = 2h
    on_error = pause
    context = handoff
    workspace = "."

    entry analyze { prompt = "..." }
    task implement { prompt = "..." }
    task test_unit { prompt = "..." }
    task test_integration { prompt = "..." }
    task review { prompt = "..." }
    exit summarize { prompt = "..." }

    analyze -> implement
    implement -> [test_unit, test_integration]
    [test_unit, test_integration] -> review
    review -> summarize when "approved"
    review -> implement when "changes needed"
}
```

### Deploy with health check
```
flow deploy {
    budget = 1h
    on_error = pause
    context = handoff
    workspace = "."

    entry build { prompt = "..." }
    task deploy_staging { prompt = "..." }
    task check_health { prompt = "..." }
    exit done { prompt = "..." }

    build -> deploy_staging
    deploy_staging -> check_health { delay = 5m }
    check_health -> done when "healthy"
    check_health -> check_health when "not healthy" { delay = 5m }
}
```

### Iterative refinement
```
flow refactor {
    budget = 3h
    on_error = pause
    context = handoff
    workspace = "."

    param target: string

    entry plan { prompt = "Analyze {{target}} and create a plan." }
    task implement { prompt = "Implement the next item from the plan." }
    task verify { prompt = "Run tests and review changes." }
    exit done { prompt = "Write a summary of all changes." }

    plan -> implement
    implement -> verify
    verify -> done when "all items complete and tests pass"
    verify -> implement when "more items remain or tests fail"
}
```
