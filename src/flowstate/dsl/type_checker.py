"""Type checker for Flowstate DSL — validates a Flow AST against all static analysis rules.

Implements the rules from specs.md Section 4:
  - S1-S8: Structural rules (graph topology)
  - E1-E9: Edge rules (edge semantics)
  - C1-C3: Cycle rules (safe cycles)
  - F1-F3: Fork-join rules (parallel region scoping)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from croniter import croniter

from flowstate.dsl.ast import (
    ContextMode,
    Edge,
    EdgeType,
    Flow,
    NodeType,
)
from flowstate.dsl.exceptions import FlowTypeError


def check_flow(flow: Flow) -> list[FlowTypeError]:
    """Validate a Flow AST against all type checking rules.

    Returns an empty list if the flow is valid, or a list of FlowTypeError
    instances describing each violation found.
    """
    errors: list[FlowTypeError] = []
    errors.extend(_check_structural(flow))
    errors.extend(_check_edges(flow))
    errors.extend(_check_cycles(flow))
    errors.extend(_check_fork_join(flow))
    errors.extend(_check_scheduling(flow))
    errors.extend(_check_sandbox(flow))
    return errors


# ---------------------------------------------------------------------------
# Shared graph utilities
# ---------------------------------------------------------------------------


def _build_adjacency(
    flow: Flow,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Build outgoing and incoming adjacency lists from edges.

    Returns (outgoing, incoming) where:
      outgoing[node_name] = list of successor node names
      incoming[node_name] = list of predecessor node names
    """
    outgoing: dict[str, list[str]] = {name: [] for name in flow.nodes}
    incoming: dict[str, list[str]] = {name: [] for name in flow.nodes}

    node_names = set(flow.nodes.keys())

    for edge in flow.edges:
        if edge.edge_type in (EdgeType.UNCONDITIONAL, EdgeType.CONDITIONAL):
            # Only add if both endpoints exist (E4 catches dangling refs)
            if (
                edge.source
                and edge.target
                and edge.source in node_names
                and edge.target in node_names
            ):
                outgoing[edge.source].append(edge.target)
                incoming[edge.target].append(edge.source)
        elif (
            edge.edge_type == EdgeType.FORK
            and edge.source
            and edge.fork_targets
            and edge.source in node_names
        ):
            for t in edge.fork_targets:
                if t in node_names:
                    outgoing[edge.source].append(t)
                    incoming[t].append(edge.source)
        elif (
            edge.edge_type == EdgeType.JOIN
            and edge.target
            and edge.join_sources
            and edge.target in node_names
        ):
            for s in edge.join_sources:
                if s in node_names:
                    outgoing[s].append(edge.target)
                    incoming[edge.target].append(s)

    return outgoing, incoming


def _reachable_from(start: str, adjacency: dict[str, list[str]]) -> set[str]:
    """BFS from start node, returning all reachable nodes (including start)."""
    visited: set[str] = set()
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        for neighbor in adjacency.get(node, []):
            queue.append(neighbor)
    return visited


def _build_outgoing_edges(flow: Flow) -> dict[str, list[Edge]]:
    """Map each node name to its list of outgoing Edge objects.

    Join edges are not counted as outgoing from their join sources — only
    unconditional, conditional, and fork edges are tracked here for the
    purpose of E1/E2/E3 classification.
    """
    result: dict[str, list[Edge]] = {name: [] for name in flow.nodes}
    for edge in flow.edges:
        if (
            edge.edge_type in (EdgeType.UNCONDITIONAL, EdgeType.CONDITIONAL, EdgeType.FORK)
            and edge.source
        ):
            result.setdefault(edge.source, []).append(edge)
    return result


def _has_default_edge(edges: list[Edge]) -> bool:
    """Check if edges form a default-edge pattern: exactly 1 unconditional + 1+ conditional."""
    unconditional = sum(1 for e in edges if e.edge_type == EdgeType.UNCONDITIONAL)
    conditional = sum(1 for e in edges if e.edge_type == EdgeType.CONDITIONAL)
    return unconditional == 1 and conditional >= 1


def _get_default_edge(edges: list[Edge]) -> Edge | None:
    """Return the default (unconditional) edge if the pattern matches, else None."""
    if not _has_default_edge(edges):
        return None
    return next(e for e in edges if e.edge_type == EdgeType.UNCONDITIONAL)


# ---------------------------------------------------------------------------
# S1-S8: Structural rules
# ---------------------------------------------------------------------------


def _check_structural(flow: Flow) -> list[FlowTypeError]:
    errors: list[FlowTypeError] = []

    # S1: Exactly one entry node
    entries = [n for n in flow.nodes.values() if n.node_type == NodeType.ENTRY]
    if len(entries) == 0:
        errors.append(FlowTypeError("S1", "Flow must have exactly one entry node, found none", ""))
    elif len(entries) > 1:
        names = ", ".join(n.name for n in entries)
        errors.append(
            FlowTypeError(
                "S1",
                f"Flow must have exactly one entry node, found {len(entries)}: {names}",
                entries[1].name,
            )
        )

    # S2: At least one exit node
    exits = [n for n in flow.nodes.values() if n.node_type == NodeType.EXIT]
    if len(exits) == 0:
        errors.append(FlowTypeError("S2", "Flow must have at least one exit node", ""))

    # Build adjacency for reachability checks
    outgoing, incoming = _build_adjacency(flow)

    # S3, S4, S6 depend on having exactly one entry node
    if len(entries) == 1:
        entry_name = entries[0].name

        # S3: All nodes reachable from entry
        reachable = _reachable_from(entry_name, outgoing)
        unreachable = set(flow.nodes.keys()) - reachable
        for name in sorted(unreachable):
            errors.append(FlowTypeError("S3", f"Node '{name}' is not reachable from entry", name))

        # S6: Entry node has no incoming edges.
        # Conditional edges targeting entry are allowed (they represent cycle
        # back-edges, e.g. Appendix A.4). Only unconditional/fork/join edges
        # targeting entry are flagged.
        if incoming.get(entry_name):
            unconditional_sources: list[str] = []
            for edge in flow.edges:
                targets_entry = False
                if edge.edge_type == EdgeType.UNCONDITIONAL and edge.target == entry_name:
                    targets_entry = True
                elif edge.edge_type == EdgeType.FORK and edge.fork_targets:
                    targets_entry = entry_name in edge.fork_targets
                elif edge.edge_type == EdgeType.JOIN and edge.target == entry_name:
                    targets_entry = True
                if targets_entry and edge.source:
                    unconditional_sources.append(edge.source)
                elif targets_entry and edge.join_sources:
                    unconditional_sources.extend(edge.join_sources)

            # Suppress S6 if entry has a default-edge pattern — it's a conditional
            # checkpoint, so unconditional back-edges are safe (the judge evaluates
            # conditional exits at the entry node).
            outgoing_edges_map = _build_outgoing_edges(flow)
            if unconditional_sources and _has_default_edge(outgoing_edges_map.get(entry_name, [])):
                unconditional_sources = []

            if unconditional_sources:
                sources = ", ".join(unconditional_sources)
                errors.append(
                    FlowTypeError(
                        "S6",
                        f"Entry node '{entry_name}' has incoming edges from: {sources}",
                        entry_name,
                    )
                )

    # S4: At least one exit reachable from every node (reverse BFS from all exits)
    if exits:
        exit_names = {n.name for n in exits}
        reverse_reachable: set[str] = set()
        queue: deque[str] = deque(exit_names)
        while queue:
            node = queue.popleft()
            if node in reverse_reachable:
                continue
            reverse_reachable.add(node)
            for pred in incoming.get(node, []):
                queue.append(pred)

        no_exit_path = set(flow.nodes.keys()) - reverse_reachable
        for name in sorted(no_exit_path):
            errors.append(FlowTypeError("S4", f"Node '{name}' cannot reach any exit node", name))

    # S7: Exit nodes have no outgoing edges
    for exit_node in exits:
        if outgoing.get(exit_node.name):
            targets = ", ".join(outgoing[exit_node.name])
            errors.append(
                FlowTypeError(
                    "S7",
                    f"Exit node '{exit_node.name}' has outgoing edges to: {targets}",
                    exit_node.name,
                )
            )

    # S8: Removed — workspace is now optional (ENGINE-026).

    # S9: Flow must declare input fields
    if not flow.input_fields:
        errors.append(
            FlowTypeError(
                "S9",
                "Flow must declare an input block with at least one field",
                flow.name,
            )
        )

    return errors


# ---------------------------------------------------------------------------
# E1-E9: Edge rules
# ---------------------------------------------------------------------------


def _effective_context(edge: Edge, flow: Flow) -> ContextMode:
    """Determine the effective context mode for an edge (explicit or inherited)."""
    return edge.config.context if edge.config.context is not None else flow.context


def _check_edges(flow: Flow) -> list[FlowTypeError]:
    errors: list[FlowTypeError] = []

    outgoing_edges = _build_outgoing_edges(flow)

    # E4: Every edge references existing nodes (run early to avoid confusing cascading errors)
    # FILE and AWAIT edges reference cross-flow targets (flow names, not node names),
    # so skip target validation for those — deferred to runtime.
    for edge in flow.edges:
        if edge.edge_type in (EdgeType.FILE, EdgeType.AWAIT):
            # Only validate the source (must be a local node); target is a flow name.
            if edge.source and edge.source not in flow.nodes:
                errors.append(
                    FlowTypeError(
                        "E4",
                        f"Edge references non-existent node '{edge.source}' as source",
                        edge.source,
                    )
                )
            continue
        if edge.source and edge.source not in flow.nodes:
            errors.append(
                FlowTypeError(
                    "E4",
                    f"Edge references non-existent node '{edge.source}' as source",
                    edge.source,
                )
            )
        if edge.target and edge.target not in flow.nodes:
            errors.append(
                FlowTypeError(
                    "E4",
                    f"Edge references non-existent node '{edge.target}' as target",
                    edge.target,
                )
            )
        if edge.fork_targets:
            for t in edge.fork_targets:
                if t not in flow.nodes:
                    errors.append(
                        FlowTypeError(
                            "E4",
                            f"Edge references non-existent node '{t}' as fork target",
                            t,
                        )
                    )
        if edge.join_sources:
            for s in edge.join_sources:
                if s not in flow.nodes:
                    errors.append(
                        FlowTypeError(
                            "E4",
                            f"Edge references non-existent node '{s}' as join source",
                            s,
                        )
                    )

    # E1, E2, E3: Classify outgoing edges per node
    for node_name, edges in outgoing_edges.items():
        if len(edges) == 0:
            continue

        has_fork = any(e.edge_type == EdgeType.FORK for e in edges)
        has_conditional = any(e.edge_type == EdgeType.CONDITIONAL for e in edges)

        # E3: No mixing fork and conditional from the same node
        if has_fork and has_conditional:
            errors.append(
                FlowTypeError(
                    "E3",
                    f"Node '{node_name}' mixes fork and conditional edges, which is not allowed",
                    node_name,
                )
            )

        if len(edges) == 1:
            edge = edges[0]
            # E1: Single outgoing edge must be unconditional (fork is also fine as
            # a single outgoing edge — it targets multiple nodes)
            if edge.edge_type == EdgeType.CONDITIONAL:
                errors.append(
                    FlowTypeError(
                        "E1",
                        f"Node '{node_name}' has exactly 1 outgoing edge, "
                        "which must be unconditional (not conditional)",
                        node_name,
                    )
                )
        elif len(edges) >= 2:
            # E2: Multiple outgoing edges must be all conditional
            # (A fork is a single Edge object, so 2+ edges should all be conditional)
            all_conditional = all(e.edge_type == EdgeType.CONDITIONAL for e in edges)
            # Skip if E3 already caught a fork+conditional mix, or if default-edge pattern
            if (
                not all_conditional
                and not (has_fork and has_conditional)
                and not _has_default_edge(edges)
            ):
                errors.append(
                    FlowTypeError(
                        "E2",
                        f"Node '{node_name}' has {len(edges)} outgoing edges: "
                        "all must be conditional (when), or use a single fork",
                        node_name,
                    )
                )

    # E5: Fork target set must match exactly one join with the same node set
    forks = [e for e in flow.edges if e.edge_type == EdgeType.FORK]
    joins = [e for e in flow.edges if e.edge_type == EdgeType.JOIN]

    for fork in forks:
        if not fork.fork_targets:
            continue
        fork_set = set(fork.fork_targets)
        matching_joins = [j for j in joins if j.join_sources and set(j.join_sources) == fork_set]
        if len(matching_joins) == 0:
            targets = ", ".join(fork.fork_targets)
            errors.append(
                FlowTypeError(
                    "E5",
                    f"Fork from '{fork.source}' to [{targets}] has no matching join edge",
                    fork.source or "",
                )
            )
        elif len(matching_joins) > 1:
            errors.append(
                FlowTypeError(
                    "E5",
                    f"Fork from '{fork.source}' has {len(matching_joins)} "
                    "matching joins (expected exactly 1)",
                    fork.source or "",
                )
            )

    # E6: Join source set must match exactly one fork's target set
    for join in joins:
        if not join.join_sources:
            continue
        join_set = set(join.join_sources)
        matching_forks = [f for f in forks if f.fork_targets and set(f.fork_targets) == join_set]
        if len(matching_forks) == 0:
            sources = ", ".join(join.join_sources)
            errors.append(
                FlowTypeError(
                    "E6",
                    f"Join to '{join.target}' from [{sources}] has no matching fork edge",
                    join.target or "",
                )
            )
        elif len(matching_forks) > 1:
            errors.append(
                FlowTypeError(
                    "E6",
                    f"Join to '{join.target}' has {len(matching_forks)} "
                    "matching forks (expected exactly 1)",
                    join.target or "",
                )
            )

    # E7: context = session not allowed on fork or join edges (effective context)
    for edge in flow.edges:
        if edge.edge_type in (EdgeType.FORK, EdgeType.JOIN):
            effective = _effective_context(edge, flow)
            if effective == ContextMode.SESSION:
                if edge.edge_type == EdgeType.FORK:
                    loc = f"fork from '{edge.source}'"
                else:
                    loc = f"join to '{edge.target}'"
                errors.append(
                    FlowTypeError(
                        "E7",
                        f"context = session is not allowed on {edge.edge_type.value} edges ({loc})",
                        loc,
                    )
                )

    # E8: delay and schedule are mutually exclusive on an edge
    for edge in flow.edges:
        if edge.config.delay_seconds is not None and edge.config.schedule is not None:
            source = edge.source or (", ".join(edge.join_sources or []))
            errors.append(
                FlowTypeError(
                    "E8",
                    f"Edge from '{source}' has both delay and schedule, "
                    "which are mutually exclusive",
                    source,
                )
            )

    # E9: schedule (cron) on an edge must be a valid cron expression
    for edge in flow.edges:
        if edge.config.schedule is not None and not croniter.is_valid(edge.config.schedule):
            errors.append(
                FlowTypeError(
                    "E9",
                    f"Edge has invalid cron expression: '{edge.config.schedule}'",
                    edge.source or "",
                )
            )

    return errors


# ---------------------------------------------------------------------------
# C1-C3: Cycle rules
# ---------------------------------------------------------------------------


def _find_cycle_edges(flow: Flow) -> list[tuple[str, str]]:
    """Find all edges (source, target) that participate in cycles.

    An edge u -> v is a cycle edge if v can reach u via the outgoing adjacency.
    Only considers nodes that exist in flow.nodes.
    """
    outgoing, _ = _build_adjacency(flow)

    cycle_edges: list[tuple[str, str]] = []
    # Collect all graph edges as (source, target) pairs, only for existing nodes
    all_edges: list[tuple[str, str]] = []
    for node_name in flow.nodes:
        for neighbor in outgoing.get(node_name, []):
            if neighbor in flow.nodes:
                all_edges.append((node_name, neighbor))

    # For each edge u -> v, check if v can reach u (forming a cycle)
    # Cache reachability to avoid redundant BFS
    reachability_cache: dict[str, set[str]] = {}
    for source, target in all_edges:
        if target not in reachability_cache:
            reachability_cache[target] = _reachable_from(target, outgoing)
        if source in reachability_cache[target]:
            cycle_edges.append((source, target))

    return cycle_edges


def _find_edge_between(flow: Flow, source: str, target: str) -> Edge | None:
    """Find an edge object connecting source to target."""
    for edge in flow.edges:
        if (
            edge.edge_type in (EdgeType.UNCONDITIONAL, EdgeType.CONDITIONAL)
            and edge.source == source
            and edge.target == target
        ):
            return edge
        if (
            edge.edge_type == EdgeType.FORK
            and edge.source == source
            and edge.fork_targets
            and target in edge.fork_targets
        ):
            return edge
        if (
            edge.edge_type == EdgeType.JOIN
            and edge.target == target
            and edge.join_sources
            and source in edge.join_sources
        ):
            return edge
    return None


def _forward_path_has_conditional(
    flow: Flow,
    start: str,
    end: str,
    outgoing: dict[str, list[str]],
    outgoing_edges_map: dict[str, list[Edge]] | None = None,
) -> bool:
    """Check if any path from start to end passes through a conditional edge.

    Uses BFS tracking whether we've seen a conditional edge on the path.
    A node with a default-edge pattern (1 unconditional + 1+ conditional) is
    treated as a conditional checkpoint — visiting it counts as seeing a
    conditional edge.
    """
    visited: set[tuple[str, bool]] = set()
    queue: deque[tuple[str, bool]] = deque([(start, False)])

    while queue:
        node, seen_cond = queue.popleft()
        if node == end:
            if seen_cond:
                return True
            continue
        state = (node, seen_cond)
        if state in visited:
            continue
        visited.add(state)

        # A node with the default-edge pattern acts as a conditional checkpoint
        if not seen_cond and outgoing_edges_map is not None:
            node_edges = outgoing_edges_map.get(node, [])
            if _has_default_edge(node_edges):
                seen_cond = True

        for neighbor in outgoing.get(node, []):
            edge = _find_edge_between(flow, node, neighbor)
            new_seen = seen_cond or (edge is not None and edge.edge_type == EdgeType.CONDITIONAL)
            queue.append((neighbor, new_seen))

    return False


def _check_cycles(flow: Flow) -> list[FlowTypeError]:
    errors: list[FlowTypeError] = []

    cycle_edges = _find_cycle_edges(flow)

    if not cycle_edges:
        return errors

    # C3: Flows with cycles must declare a budget > 0
    if flow.budget_seconds <= 0:
        errors.append(
            FlowTypeError(
                "C3",
                "Flows with cycles must declare a budget (budget must be > 0)",
                "",
            )
        )

    # Build fork group membership for C1
    fork_group_members: set[str] = set()
    for edge in flow.edges:
        if edge.edge_type == EdgeType.FORK and edge.fork_targets:
            fork_group_members.update(edge.fork_targets)

    outgoing, _ = _build_adjacency(flow)

    # C1: Check each cycle edge — if a conditional or unconditional edge targets
    # a fork-join group member, flag it. Fork and join edges themselves are
    # structural and define the fork-join group, so they are excluded from
    # this check. Only "re-entry" edges (conditional/unconditional back-edges
    # that close a cycle by targeting a fork group member) are flagged.
    #
    # Build a set of (source, target) pairs from fork/join edges to exclude them.
    fork_join_edge_pairs: set[tuple[str, str]] = set()
    for edge in flow.edges:
        if edge.edge_type == EdgeType.FORK and edge.source and edge.fork_targets:
            for t in edge.fork_targets:
                fork_join_edge_pairs.add((edge.source, t))
        elif edge.edge_type == EdgeType.JOIN and edge.target and edge.join_sources:
            for s in edge.join_sources:
                fork_join_edge_pairs.add((s, edge.target))

    c1_reported: set[tuple[str, str]] = set()
    for source, target in cycle_edges:
        # Skip fork/join structural edges
        if (source, target) in fork_join_edge_pairs:
            continue
        if target in fork_group_members:
            key = (source, target)
            if key not in c1_reported:
                c1_reported.add(key)
                errors.append(
                    FlowTypeError(
                        "C1",
                        f"Cycle edge '{source}' -> '{target}' targets node '{target}' "
                        "which is inside a fork-join group. "
                        "Cycle targets must be outside fork-join groups.",
                        target,
                    )
                )

    # C2: Every cycle must pass through at least one conditional edge.
    # Identify unique cycles by their back-edges (edges where target can reach source).
    # For each such edge, check if the cycle path contains a conditional edge.
    # Deduplicate by (source, target) pair of the cycle-closing edge.
    #
    # A node with a default-edge pattern (1 unconditional + 1+ conditional) acts
    # as a conditional checkpoint — the judge evaluates conditional exits there.
    outgoing_edges_map = _build_outgoing_edges(flow)

    c2_reported: set[tuple[str, str]] = set()
    for source, target in cycle_edges:
        # Check if the edge itself is conditional
        edge_obj = _find_edge_between(flow, source, target)
        if edge_obj and edge_obj.edge_type == EdgeType.CONDITIONAL:
            continue  # This edge in the cycle is conditional

        # Check if source node has default-edge pattern (conditional checkpoint)
        source_edges = outgoing_edges_map.get(source, [])
        if _has_default_edge(source_edges):
            continue

        # Check the rest of the cycle path (from target back to source)
        if _forward_path_has_conditional(flow, target, source, outgoing, outgoing_edges_map):
            continue  # The cycle has a conditional edge somewhere

        # This edge is part of an all-unconditional cycle
        # Only report once per unique cycle-closing edge
        key = (source, target)
        if key not in c2_reported:
            c2_reported.add(key)
            errors.append(
                FlowTypeError(
                    "C2",
                    f"Cycle from '{source}' back to '{target}' has no conditional edge. "
                    "Every cycle must pass through at least one conditional edge "
                    "to prevent infinite loops.",
                    source,
                )
            )

    return errors


# ---------------------------------------------------------------------------
# F1-F3: Fork-join rules
# ---------------------------------------------------------------------------


@dataclass
class _ForkJoinGroup:
    """Represents a matched fork-join pair."""

    fork_source: str
    members: set[str]
    join_target: str
    fork_edge: Edge
    join_edge: Edge


def _identify_fork_join_groups(flow: Flow) -> list[_ForkJoinGroup]:
    """Identify matched fork-join groups in the flow."""
    forks = [e for e in flow.edges if e.edge_type == EdgeType.FORK]
    joins = [e for e in flow.edges if e.edge_type == EdgeType.JOIN]

    groups: list[_ForkJoinGroup] = []
    for fork in forks:
        if not fork.fork_targets:
            continue
        fork_set = set(fork.fork_targets)
        for join in joins:
            if join.join_sources and set(join.join_sources) == fork_set:
                groups.append(
                    _ForkJoinGroup(
                        fork_source=fork.source or "",
                        members=fork_set,
                        join_target=join.target or "",
                        fork_edge=fork,
                        join_edge=join,
                    )
                )
                break
    return groups


def _check_fork_join(flow: Flow) -> list[FlowTypeError]:
    errors: list[FlowTypeError] = []

    groups = _identify_fork_join_groups(flow)

    # F1: Fork groups may nest but must not share member nodes
    for i, g1 in enumerate(groups):
        for g2 in groups[i + 1 :]:
            intersection = g1.members & g2.members
            if intersection:
                overlap = ", ".join(sorted(intersection))
                errors.append(
                    FlowTypeError(
                        "F1",
                        f"Fork groups overlap: fork from '{g1.fork_source}' and fork from "
                        f"'{g2.fork_source}' share member nodes [{overlap}]. "
                        "Fork groups may nest but must not share member nodes.",
                        overlap,
                    )
                )

    # F2: A join node cannot also be a fork source in the same declaration.
    # The grammar prevents this syntactically, but check for programmatically
    # constructed flows where a single Edge has both join_sources and fork_targets.
    for edge in flow.edges:
        if edge.join_sources and edge.fork_targets:
            errors.append(
                FlowTypeError(
                    "F2",
                    f"Edge combines join (from [{', '.join(edge.join_sources)}]) "
                    f"and fork (to [{', '.join(edge.fork_targets)}]) in a single declaration. "
                    "Separate join and fork into distinct edge declarations.",
                    edge.target or "",
                )
            )

    # F3: Fork targets must converge to a single join.
    # Verify that all fork target nodes can reach the join target node.
    outgoing, _ = _build_adjacency(flow)
    for group in groups:
        for member in sorted(group.members):
            reachable = _reachable_from(member, outgoing)
            if group.join_target not in reachable:
                errors.append(
                    FlowTypeError(
                        "F3",
                        f"Fork target '{member}' (forked from '{group.fork_source}') "
                        f"cannot reach join node '{group.join_target}'. "
                        "All fork targets must converge to a single join.",
                        member,
                    )
                )

    return errors


# ---------------------------------------------------------------------------
# P1-P4: Scheduling and parallelism rules
# ---------------------------------------------------------------------------


def _check_scheduling(flow: Flow) -> list[FlowTypeError]:
    errors: list[FlowTypeError] = []

    # P1: max_parallel must be >= 1
    if flow.max_parallel < 1:
        errors.append(
            FlowTypeError(
                "P1",
                f"max_parallel must be >= 1, got {flow.max_parallel}",
                "",
            )
        )

    for node in flow.nodes.values():
        # P2: Wait nodes must have exactly one of delay or until (not both, not neither)
        if node.node_type == NodeType.WAIT:
            has_delay = node.wait_delay_seconds is not None
            has_until = node.wait_until_cron is not None
            if has_delay and has_until:
                errors.append(
                    FlowTypeError(
                        "P2",
                        f"Wait node '{node.name}' has both delay and until; "
                        "exactly one is required",
                        node.name,
                    )
                )
            elif not has_delay and not has_until:
                errors.append(
                    FlowTypeError(
                        "P2",
                        f"Wait node '{node.name}' has neither delay nor until; "
                        "exactly one is required",
                        node.name,
                    )
                )
            # Validate cron expression if until is specified
            if (
                has_until
                and node.wait_until_cron is not None
                and not croniter.is_valid(node.wait_until_cron)
            ):
                errors.append(
                    FlowTypeError(
                        "P2",
                        f"Wait node '{node.name}' has invalid cron expression: "
                        f"'{node.wait_until_cron}'",
                        node.name,
                    )
                )

        # P3: Fence nodes must not have a prompt
        if node.node_type == NodeType.FENCE and node.prompt:
            errors.append(
                FlowTypeError(
                    "P3",
                    f"Fence node '{node.name}' must not have a prompt",
                    node.name,
                )
            )

        # P4: Atomic nodes must have a prompt
        if node.node_type == NodeType.ATOMIC and not node.prompt:
            errors.append(
                FlowTypeError(
                    "P4",
                    f"Atomic node '{node.name}' must have a prompt",
                    node.name,
                )
            )

    return errors


# ---------------------------------------------------------------------------
# SB1: Sandbox rules
# ---------------------------------------------------------------------------


def _check_sandbox(flow: Flow) -> list[FlowTypeError]:
    errors: list[FlowTypeError] = []

    # SB1 (flow level): sandbox_policy requires sandbox = true
    if flow.sandbox_policy is not None and not flow.sandbox:
        errors.append(
            FlowTypeError(
                "SB1",
                "sandbox_policy requires sandbox = true at flow level",
                flow.name,
            )
        )

    # SB1 (node level): sandbox_policy requires sandbox = true (explicit or inherited)
    for node in flow.nodes.values():
        if node.sandbox_policy is not None:
            # Determine the effective sandbox value for this node
            effective_sandbox = node.sandbox if node.sandbox is not None else flow.sandbox
            if not effective_sandbox:
                errors.append(
                    FlowTypeError(
                        "SB1",
                        f"Node '{node.name}' sets sandbox_policy but sandbox is not enabled"
                        " (sandbox must be true, either on the node or inherited from flow)",
                        node.name,
                    )
                )

    return errors
