# Sprint 37 — Personas, Lumon block, and persistent worktrees

**Issues**: DSL-015, ENGINE-086, SHARED-012, DSL-016, ENGINE-087, DSL-017, STATE-013, ENGINE-088
**Domains**: dsl, engine, shared, state
**Date**: 2026-05-10
**Phase**: 37 (three independent feature tracks: a, b, c)

## Goal

Three user-facing capabilities land together:

- **37a — Reusable personas.** A node can declare `agent = "name"`, and an `agent.md` file (resolved via Claude Code's standard precedence) drives the subprocess as a system prompt. Typos surface as type errors at parse time, not runtime.
- **37b — `lumon { ... }` block.** A flow or node can declare `lumon { enabled = true, plugins = ["filesystem"] }` and the engine writes exactly that plugin set (plus the always-on built-in `flowstate` plugin) into the worktree's `.lumon.json`. Flat `lumon = true` / `sandbox_policy = "x"` syntax keeps parsing identically.
- **37c — Persistent exit worktrees.** A flow with `worktree_persist = true` merges the exit node's branch back into the original workspace's source branch on success. The merge runs in a fresh detached temp worktree — the user's main checkout is never touched, even if dirty. Conflicts preserve the exit branch and mark the run `completed_with_conflicts`.

End state: a single flow can declare a persona-driven advisor, deploy a curated plugin set, and persist commits back to `main` without ever switching the user's branch or trampling their working tree.

## Track Independence

The three tracks share no files. They land in any order.

- **37a (DSL-015 → ENGINE-086):** `dsl/{ast,grammar.lark,parser,type_checker}.py`, `engine/{context,executor,harness,subprocess_mgr}.py`
- **37b (SHARED-012 → DSL-016 → ENGINE-087):** `dsl/{ast,grammar.lark,parser,type_checker}.py`, `engine/lumon.py`. SHARED-012 is the AST-shape change and must land before DSL-016 and ENGINE-087.
- **37c (DSL-017, STATE-013 → ENGINE-088):** `dsl/{ast,grammar.lark,parser,type_checker}.py`, `state/{schema,models,repository}.py`, `engine/{executor,worktree,events}.py`

37a and 37b both touch `dsl/ast.py`, `dsl/grammar.lark`, `dsl/parser.py`, `dsl/type_checker.py`. If parallel agents work on these tracks, use `isolation: "worktree"` and serialize the merge.

---

## Acceptance Tests

Tests prefixed **[UNIT]** are pytest unit tests. **[E2E]** require a real running server / real git repos. **[GREP]** are static checks against the source tree. **[CHECK]** invoke `/check` against a `.flow` file.

---

### Phase 37a — Reusable `agent.md` persona references

#### DSL-015: `agent` node attribute + AG1/AG2 type-check rules

**TEST-37a.1: `agent = "name"` parses at every node type** [UNIT]
  Given: Fixture `.flow` files where an entry, task, exit, and atomic node each set `agent = "demo"` and have a sibling `agents/demo.md`
  When: Each is parsed
  Then: For each node, the resulting `Node.agent == "demo"`. Nodes without the attribute have `Node.agent is None`.

**TEST-37a.2: Resolved persona in `<flow_dir>/agents/<name>.md` type-checks clean** [CHECK]
  Given: `flows/test_agent.flow` with `task t1 { agent = "helly" prompt = "..." }` and `flows/agents/helly.md` with valid YAML frontmatter and a body
  When: Running `/check flows/test_agent.flow`
  Then: Exit code 0; no errors

**TEST-37a.3: Resolved persona in `~/.claude/agents/<name>.md` also type-checks clean** [CHECK]
  Given: `flows/test_agent.flow` with `agent = "shared_persona"`, no sibling `flows/agents/shared_persona.md`, but a real `~/.claude/agents/shared_persona.md`
  When: Running `/check flows/test_agent.flow`
  Then: Exit code 0; no errors. The flow-local lookup falls through to the user-global lookup.

**TEST-37a.4: AG1 fires when persona file is missing** [CHECK]
  Given: A flow with `agent = "definitely_not_a_persona"` and no matching file in either lookup location
  When: Running `/check`
  Then: Exit code non-zero. The error message contains the literal `AG1`, the persona name, AND mentions both lookup paths (`<flow_dir>/agents/...` and `~/.claude/agents/...`)

**TEST-37a.5: AG2 fires on malformed YAML frontmatter** [CHECK]
  Given: A persona file whose frontmatter starts with `---` but contains malformed YAML (e.g. `name: [unterminated`)
  When: Running `/check`
  Then: Exit code non-zero. The error message contains the literal `AG2`, the persona name, and the path to the malformed file.

**TEST-37a.6: Persona file without frontmatter type-checks clean** [CHECK]
  Given: A persona file containing only a body (no `---` markers)
  When: Running `/check`
  Then: Exit code 0; no errors

#### ENGINE-086: Persona drives subprocess as system prompt

**TEST-37a.7: Subprocess is launched with persona body as system prompt** [E2E]
  Given: A running server, a flow with one task `task helly { agent = "helly" prompt = "Topic: {{topic}}" }`, and `agents/helly.md` containing `---\nname: Helly R.\n---\n\nYou are Helly R., a relentless challenger. Push back on flimsy reasoning.`
  When: Submitting a run with `topic = "should I refactor X"` and inspecting the subprocess invocation (logs OR a captured argv) for that task
  Then: The system-prompt argument passed to the subprocess equals the persona body (post-frontmatter strip), with `{{topic}}` expanded to `should I refactor X`. The kickoff/user message is the rendered `prompt` (`"Topic: should I refactor X"`), distinct from the system prompt.

**TEST-37a.8: Template variables expand in BOTH system prompt and kickoff** [UNIT]
  Given: A persona body containing `{{topic}}` and a node `prompt` containing `{{topic}}`, with `params = {"topic": "X"}`
  When: `load_agent_persona` is called and the executor builds the kickoff message
  Then: Both the returned `AgentPersona.system_prompt` and the kickoff message contain `X`, neither contains the literal `{{topic}}`

**TEST-37a.9: Persona-less nodes follow the legacy dispatch path** [UNIT]
  Given: A flow whose nodes have no `agent` attribute
  When: A run executes
  Then: The harness is dispatched via the existing `run_task(...)` path, NOT via the system-prompt variant. No regression in any pre-existing engine test.

**TEST-37a.10: File deleted between type-check and execution fails the task** [E2E]
  Given: A type-checked flow with `agent = "ghost"` and `agents/ghost.md` exists at submission time, but the file is deleted before the task starts
  When: The task executes
  Then: The task fails with a clear error referencing the missing persona (`AgentPersonaError` or equivalent). The error mentions `ghost`. The run does NOT silently fall back to a no-system-prompt invocation.

**TEST-37a.11: Frontmatter `model:` selects the matching harness when registered** [UNIT]
  Given: A persona with `model: subprocess` (a registered harness name) on a node whose `harness` attribute would otherwise resolve to a different harness
  When: The executor selects the harness for that task
  Then: The `subprocess` harness is used. When the model is unregistered (`model: bogus_harness_xyz`), a warning is logged and the harness falls back to `node.harness or flow.harness`.

---

### Phase 37b — `lumon { ... }` config block

#### SHARED-012: `LumonConfig` AST migration

**TEST-37b.1: AST exposes `LumonConfig` and removes flat fields** [UNIT]
  Given: The post-migration `flowstate.dsl.ast` module
  When: Inspecting `Flow` and `Node` dataclass fields
  Then: Both have a single `lumon: LumonConfig | None` field. None of `Flow`/`Node` has `sandbox`, `sandbox_policy`, `lumon_config`, or a flat `lumon: bool`.

**TEST-37b.2: All four legacy flat syntactic forms produce equivalent ASTs** [UNIT]
  Given: Four single-flow source strings: `lumon = true` / `sandbox = true` / `lumon = true\nlumon_config = "p.json"` / `sandbox = true\nsandbox_policy = "p.json"`
  When: Each is parsed
  Then: The first two produce `Flow.lumon == LumonConfig(enabled=True, plugins=None, config_path=None)`. The latter two both produce `Flow.lumon == LumonConfig(enabled=True, plugins=None, config_path="p.json")`.

**TEST-37b.3: Engine reads new shape with no behavior change for flat-syntax flows** [E2E]
  Given: Any pre-existing `flows/*.flow` that uses the flat `lumon = true` syntax
  When: A run is submitted on it
  Then: The worktree's `.lumon.json` is identical (byte-for-byte) to what was produced before SHARED-012, except that the file path resolution still works. (Implementation latitude: order of plugin keys may differ, but the set is identical.)

**TEST-37b.4: No code outside the parser references removed flat fields** [GREP]
  Given: The post-migration source tree
  When: Running `grep -rn -E '\.lumon_config\b|\.sandbox_policy\b|\.sandbox\b' src/flowstate/ --include='*.py' | grep -vE '^src/flowstate/dsl/parser\.py:'`
  Then: Zero matches in non-parser code that read the removed fields. (Matches in comments or in `dsl/parser.py` are allowed.)

#### DSL-016: Block grammar + L1/L2/L3 type-check rules

**TEST-37b.5: `lumon { enabled = true, plugins = ["filesystem"] }` parses at flow level** [UNIT]
  Given: A flow with that block
  When: Parsing
  Then: `Flow.lumon == LumonConfig(enabled=True, plugins=("filesystem",), config_path=None)`

**TEST-37b.6: Same block parses at node level and overrides flow-level config** [UNIT]
  Given: A flow with `lumon { enabled = true, plugins = ["a"] }` at flow level, and one task with `lumon { enabled = true, plugins = ["b"] }` and another task with no lumon block
  When: Parsing
  Then: `Flow.lumon.plugins == ("a",)`, the overriding `Node.lumon.plugins == ("b",)`, the inheriting `Node.lumon is None`

**TEST-37b.7: L1 fires when `plugins` (or `config`) appears without `enabled = true` in scope** [CHECK]
  Given: A flow with `lumon { plugins = ["filesystem"] }` (no `enabled = true`)
  When: Running `/check`
  Then: Exit code non-zero. Error message contains `L1`.

**TEST-37b.8: L2 fires when `plugins` and `config` are both set in the same block** [CHECK]
  Given: A flow with `lumon { enabled = true, plugins = ["filesystem"], config = "policy.json" }`
  When: Running `/check`
  Then: Exit code non-zero. Error message contains `L2`.

**TEST-37b.9: L3 fires for unresolved plugin names** [CHECK]
  Given: A flow with `lumon { enabled = true, plugins = ["definitely_not_a_plugin"] }` and no plugin directory by that name in `<flow_dir>/plugins/`, `~/.flowstate/plugins/`, or built-in
  When: Running `/check`
  Then: Exit code non-zero. Error contains `L3` and the plugin name. Mentions all three lookup locations.

**TEST-37b.10: Mixing block syntax with flat syntax in the same scope is a parse error** [CHECK]
  Given: A flow with both `lumon = true` and `lumon { enabled = true }`
  When: Parsing or running `/check`
  Then: Exit code non-zero. Error mentions that block and flat syntax are mutually exclusive.

#### ENGINE-087: `.lumon.json` synthesis from `plugins` list

**TEST-37b.11: `plugins`-list synthesis writes exactly those plugins plus built-in flowstate** [E2E]
  Given: A running server, a flow with `lumon { enabled = true, plugins = ["filesystem"] }`, plugin dirs for `filesystem` resolvable on disk
  When: A task in that flow runs to the point where the worktree is set up
  Then: `<worktree>/.lumon.json` parses as JSON. Its `plugins` keys equal exactly the set `{"filesystem", "flowstate"}`. No other plugin keys are present.

**TEST-37b.12: Multi-plugin synthesis preserves all listed names** [E2E]
  Given: A flow with `lumon { enabled = true, plugins = ["filesystem", "git"] }` and both plugin dirs resolvable
  When: A task runs and the worktree is set up
  Then: `<worktree>/.lumon.json`'s `plugins` keys equal exactly `{"filesystem", "git", "flowstate"}`

**TEST-37b.13: `config` path branch still loads `.lumon.json` from disk** [E2E]
  Given: A flow with `lumon { enabled = true, config = "policy.json" }` and `policy.json` next to the flow file with `{"plugins": {"custom": {}}}`
  When: A task runs
  Then: `<worktree>/.lumon.json`'s `plugins` keys are `{"custom", "flowstate"}` (custom from the file, flowstate always merged in)

**TEST-37b.14: Node-level `lumon` fully overrides flow-level (not merged)** [UNIT]
  Given: A flow with `lumon { enabled = true, config = "flow.json" }` at flow level and `lumon { enabled = true, plugins = ["x"] }` at node level
  When: Resolving the effective config for that node and writing `.lumon.json`
  Then: The output `plugins` keys are `{"x", "flowstate"}`. The flow-level `config` is NOT loaded for this node.

**TEST-37b.15: Node-level `enabled = false` disables lumon despite flow-level enabled** [UNIT]
  Given: A flow with `lumon { enabled = true, plugins = ["a"] }` and a node with `lumon { enabled = false }`
  When: Resolving the effective config for that node
  Then: `_use_lumon(flow, node) is False`. No `.lumon.json` is written for that node's worktree (or the lumon path is skipped).

---

### Phase 37c — Persist exit worktree to source branch

#### DSL-017: `worktree_persist` flow attribute + WP1

**TEST-37c.1: `worktree_persist = true` parses at flow level** [UNIT]
  Given: A flow with `worktree = true` and `worktree_persist = true`
  When: Parsing
  Then: `Flow.worktree_persist is True`. Default for omitted attribute is `False`.

**TEST-37c.2: WP1 fires when `worktree_persist = true` and `worktree = false`** [CHECK]
  Given: A flow with `worktree = false` and `worktree_persist = true`
  When: Running `/check`
  Then: Exit code non-zero. Error contains `WP1` and the message references both attribute names.

#### STATE-013: `source_branch` column

**TEST-37c.3: Schema has `source_branch TEXT` nullable column on `flow_runs`** [UNIT]
  Given: A fresh DB after migrations run
  When: Inspecting the `flow_runs` schema via `PRAGMA table_info(flow_runs)`
  Then: A `source_branch` column exists, type `TEXT`, `NOT NULL = 0` (nullable), no default

**TEST-37c.4: Migration is additive on a pre-existing DB** [UNIT]
  Given: A SQLite DB at the previous schema version with at least one existing `flow_runs` row
  When: Migrations run
  Then: Migration succeeds. The pre-existing row's `source_branch` is `NULL`. No row was lost or rewritten.

**TEST-37c.5: `set_source_branch` / `get_source_branch` round-trip** [UNIT]
  Given: A repository with one flow_run row
  When: `repo.set_source_branch(run_id, "feature/x")`, then `repo.get_source_branch(run_id)`
  Then: Returns `"feature/x"`. Calling `set_source_branch(run_id, None)` then `get_source_branch` returns `None`.

#### ENGINE-088: Detached-worktree merge with CAS + lock + conflict preservation

**TEST-37c.6: Source branch captured at run-start when `worktree_persist = true`** [E2E]
  Given: A real git repo at `tmp/journal` checked out on `main`, a flow with `worktree = true, worktree_persist = true, workspace = "tmp/journal"`
  When: A run is submitted
  Then: Within seconds of run-start, the `flow_runs` row's `source_branch` is `"main"`. (Inspected via `sqlite3` direct query against the DB.)

**TEST-37c.7: Detached HEAD at run-start records NULL `source_branch`** [E2E]
  Given: The workspace is in detached HEAD state at run-start (`git checkout <commit-sha>` before submit), `worktree_persist = true`
  When: A run is submitted
  Then: A warning is logged. `source_branch` in the DB row is `NULL`. The run still starts and proceeds normally.

**TEST-37c.8: Successful merge advances source branch in the original workspace** [E2E]
  Given: A `tmp/journal` repo on `main` at commit C0, a flow with `worktree_persist = true` whose exit task makes one commit C1 in its worktree branch
  When: The run completes successfully
  Then: `git rev-parse main` in `tmp/journal` returns a new commit M (the merge commit) whose `^1` is C0 and whose `^2` is C1. The run row's status is `completed`. A `SOURCE_BRANCH_ADVANCED` event was emitted with `old_commit = C0` and `new_commit = M`.

**TEST-37c.9: User's working tree is undisturbed by the merge** [E2E]
  Given: Mid-run, the user stages a tracked file (`echo modified > tracked.txt; git add tracked.txt` in `tmp/journal`) and creates an untracked file (`echo notes > scratch.md`). Run continues to completion with `worktree_persist = true`.
  When: The run completes
  Then: `git status --porcelain` in `tmp/journal` STILL shows the staged `tracked.txt` and untracked `scratch.md`. The HEAD ref is unchanged (still on `main`). `git symbolic-ref --short HEAD` returns `main`. The user's checkout was never `git checkout`-ed or `git reset`-ed.

**TEST-37c.10: Merge conflict preserves exit branch and marks run `completed_with_conflicts`** [E2E]
  Given: A flow's exit-task worktree commits a change to `file.txt`, AND between run-start and run-completion, `main` in the original workspace is also modified on the same line of `file.txt` (simulated by making a commit on `main` while the run is in progress)
  When: The run reaches completion and `_persist_exit_worktree` runs
  Then: `git rev-parse main` is unchanged from what the user committed (no flowstate merge commit). The run row's status is `completed_with_conflicts`. The exit task's worktree branch (`flowstate/<run-id>/exit-*` or whatever the cleanup pattern is) STILL exists in `git branch --list` (was NOT cleaned up). A `SOURCE_BRANCH_PERSIST_CONFLICT` event was emitted, and its payload includes the preserved branch name AND a non-empty `conflict_files` list.

**TEST-37c.11: CAS retry succeeds when the source branch moves but doesn't conflict** [UNIT]
  Given: A real `tmp_path` git repo with `main` at C0. The persist helper is invoked. Between the helper's `git rev-parse main` (returning C0) and its `git update-ref` CAS, an external process advances `main` to C1 (a commit on a different file from the exit branch's changes). `max_cas_retries = 3`.
  When: The helper runs
  Then: First CAS attempt fails. The helper retries with a fresh temp worktree based on C1, the merge succeeds with no conflicts, the second CAS succeeds. Final `main` is the merge of C1 and the exit branch. `PersistResult.status == "advanced"`.

**TEST-37c.12: CAS exhaustion preserves exit branch like a conflict** [UNIT]
  Given: A repo where `main` is mutated between every `rev-parse` and `update-ref` such that all 3 CAS attempts fail
  When: The helper runs
  Then: `PersistResult.status == "cas_exhausted"`. The exit branch is preserved (cleanup skipped). Run status is `completed_with_conflicts`.

**TEST-37c.13: Concurrent runs in the same workspace serialize on the file lock** [UNIT]
  Given: Two concurrent invocations of `merge_to_source_branch_via_detached_worktree` for the same `original_workspace`, with sleeps inserted between rev-parse and update-ref to make races likely
  When: Both run
  Then: Both eventually complete with `status == "advanced"`. `main` ends up at a chain that includes both exit branches' commits (one merge stacked on the other, no lost work). The lock file at `<workspace>/.git/flowstate-persist.lock` exists during execution.

**TEST-37c.14: `worktree_persist = false` flows behave unchanged** [E2E]
  Given: A flow with `worktree = true` and `worktree_persist` omitted (or `false`)
  When: A run completes successfully
  Then: `source_branch` was never set (DB row is `NULL`). No `SOURCE_BRANCH_ADVANCED` or `SOURCE_BRANCH_PERSIST_CONFLICT` event was emitted. The run row's status is `completed`. Worktrees and branches are cleaned up exactly as before this sprint.

**TEST-37c.15: Skip cases do not error and produce a documented reason** [UNIT]
  Given: Each of these conditions, one at a time: (a) `source_branch` NULL on the run, (b) no exit task with status `completed`, (c) exit reached via a `context = none` edge, (d) no `worktree` artifact on the exit task
  When: `_persist_exit_worktree` is called
  Then: Returns a `PersistResult` with `status == "skipped"` and a non-empty `reason`. No exception escapes. Run status is `completed` (NOT `completed_with_conflicts`). No `SOURCE_BRANCH_ADVANCED` event is emitted.

**TEST-37c.16: Two new event types registered and exhaustively tested** [UNIT]
  Given: The `EventType` enum after this sprint
  When: Inspecting its members
  Then: `SOURCE_BRANCH_ADVANCED` and `SOURCE_BRANCH_PERSIST_CONFLICT` are present. The event-count regression test (from ENGINE-085) is updated to reflect the +2 count and passes.

---

## Out of Scope

- **Per-node tool restriction from `agent.md` frontmatter `tools: [...]`** — read into logs only; not enforced. Future work.
- **Mapping Anthropic model IDs (e.g. `claude-opus-4-7`) to harnesses in `agent.md` frontmatter** — `model:` resolves to a registered harness name, not an Anthropic model ID. Spec note only.
- **Persona reload / hot-reload** — personas are read at task start; editing `agent.md` mid-run does not affect the in-flight task.
- **Lumon plugin manifest validation beyond directory existence** — L3 only checks that `<plugin>/` is a directory. Manifest schema validation is future work.
- **Merging plugin lists across flow-level and node-level lumon blocks** — node-level fully overrides (as documented). Future work could add a `merge` mode.
- **Auto-fast-forward without merge commit** — persist always uses `git merge --no-ff`. Linear-history mode is future work.
- **Cross-workspace persist coordination** — the file lock is per-workspace. Two flowstate processes targeting two different clones of the same upstream repo do not coordinate; that's the user's problem.
- **NFS / non-flock filesystems** — persist works best-effort without `flock`. Single-user local dev/desktop is the supported target.
- **UI surface for `completed_with_conflicts`** — the event is emitted and the run status is correct in the DB; UI styling/badges for the new status are tracked separately if needed.
- **Backfilling `source_branch` for pre-existing runs** — the migration is additive; old rows stay NULL.

---

## Integration Points

- **DSL-015 → ENGINE-086 (persona):**
  - DSL produces: `Node.agent: str | None`. Type checker has validated the file exists at parse time.
  - Engine consumes: reads `node.agent`, resolves the same way DSL did (shared resolver in `dsl/agent_resolver` or duplicate the small function), parses frontmatter at run-time, dispatches via the harness's system-prompt variant.
  - Shared contract: persona resolution precedence is `<flow_dir>/agents/<name>.md` then `~/.claude/agents/<name>.md`. Same in DSL and engine.

- **SHARED-012 → DSL-016 → ENGINE-087 (lumon):**
  - SHARED-012 produces: `LumonConfig` dataclass; `Flow.lumon: LumonConfig | None`; `Node.lumon: LumonConfig | None`. Parser-layer compat translates flat syntax to `LumonConfig`. Engine adapted to read the new shape but plugin-list synthesis is deferred.
  - DSL-016 consumes SHARED-012's AST. Adds block-grammar parser path. Adds L1/L2/L3 to type checker. Mixed-syntax in same scope is a parse error.
  - ENGINE-087 consumes both. Reads `_effective_lumon_config(flow, node)` (node fully overrides flow). When `plugins` is set, synthesizes `.lumon.json` in-memory; when `config_path` is set, loads from disk. Built-in `flowstate` plugin always merged in.
  - Shared contract: `LumonConfig.plugins: tuple[str, ...] | None`. `None` = "not specified". `()` = "explicitly no plugins beyond flowstate". Non-empty = exactly those plugins (plus flowstate).

- **DSL-017 + STATE-013 → ENGINE-088 (worktree persist):**
  - DSL-017 produces: `Flow.worktree_persist: bool` (default `False`). WP1 ensures consistency with `worktree`.
  - STATE-013 produces: `flow_runs.source_branch TEXT NULL` column; `set_source_branch` / `get_source_branch`. `FlowRunRow.source_branch: str | None`.
  - ENGINE-088 consumes both. At run-start: if `flow.worktree_persist`, capture `git symbolic-ref --short HEAD` of original workspace and call `set_source_branch`. At completion: if `flow.worktree_persist`, run `_persist_exit_worktree` which reads `get_source_branch`, finds the exit task's worktree artifact, and merges via a detached temp worktree under `flock` with `git update-ref` CAS retries.
  - New events: `SOURCE_BRANCH_ADVANCED`, `SOURCE_BRANCH_PERSIST_CONFLICT`. New flow-run status: `completed_with_conflicts`.

---

## Risks and Concerns

- **Concurrent dsl/* edits.** 37a (DSL-015) and 37b (DSL-016, SHARED-012) and 37c (DSL-017) all touch `dsl/ast.py`, `dsl/grammar.lark`, `dsl/parser.py`, `dsl/type_checker.py`. Recommendation: run these three DSL changes through a single dsl-dev agent in sequence (SHARED-012 → DSL-016 → DSL-015 → DSL-017 — order is flexible since they don't interfere semantically), OR use isolated worktrees and merge carefully. Parallel ungated dsl-dev agents WILL conflict on these files.

- **Harness protocol gap.** ENGINE-086 needs `run_task_with_system_prompt` exposed via the harness protocol. Today only `SubprocessManager` implements it (used by judge). For non-subprocess harnesses (ACP, SDK), the issue says: raise `NotImplementedError` with a clear message rather than silently fall back. Evaluator must verify this explicit error path: when an ACP-or-SDK harness runs an `agent`-using node, the task fails clearly (not silently). This is captured by TEST-37a.10's spirit but warrants an explicit subtest if those harnesses are wired in dev.

- **`_complete_flow` sync vs async.** ENGINE-088's persist call is async; `_complete_flow` is currently sync. The implementing agent must thread `await` correctly, possibly extracting the persist into the async caller of `_complete_flow`. If done wrong, persist runs in a separate event loop or never runs. TEST-37c.8 (E2E) catches this by inspecting `git log` post-completion.

- **Lumon block + flat-syntax precedence test coverage gap.** Spec for SHARED-012 says `lumon_config` wins over `sandbox_policy` when both are set. This sprint preserves that, but if DSL-016's mixed-syntax parse error fires too eagerly (e.g. on AST that combines flat and block from inheritance), it could break TEST-37b.3 (regression for flat-syntax flows). Mixed-syntax check should be **per-scope** (within a single flow_decl OR a single node body), NOT cross-scope (flow has flat, node has block — that's allowed, since node fully overrides flow).

- **`flock` on macOS dev machines.** macOS supports `fcntl.flock` natively. Linux does too. The single-user local-dev assumption is correct for this sprint. No risk.

- **CAS retry test determinism.** TEST-37c.11 races a real git mutation against the helper. Use a deterministic hook or instrument the helper with a `_pre_cas_hook` injection point so the test can mutate `main` between rev-parse and update-ref reliably. Without a hook, the test will be flaky.

---

## Done Criteria

This sprint is complete when:

- All 37 acceptance tests above PASS in the evaluator's verdict (numbered TEST-37a.1 through TEST-37c.16).
- Each issue's E2E Verification Log section contains real commands and observed output, not placeholders.
- `/test` passes with no regressions vs the Phase 36 baseline.
- `/lint` passes (ruff, pyright, eslint — though ENGINE-086/087/088 and the rest are Python-only this sprint).
- `/check` passes on every existing `flows/*.flow` (regression check for the lumon parser-layer compat).
- The 8 issue files have status `done` in their frontmatter and in `issues/PLAN.md`.
- Spec section 3.2 / 3.4 / 4 / 9.7 / 9.9 / 11.1 are updated to reflect the new attributes, block syntax, type-check rules, AST shape, and persist mechanism.
