from __future__ import annotations

from pathlib import Path
from typing import Any

from lark import Lark, Token, Transformer, v_args
from lark.exceptions import UnexpectedInput, VisitError

from flowstate.dsl.ast import (
    ContextMode,
    Edge,
    EdgeConfig,
    EdgeType,
    ErrorPolicy,
    Flow,
    Node,
    NodeType,
    OverlapPolicy,
    Param,
    ParamType,
)
from flowstate.dsl.exceptions import FlowParseError

_GRAMMAR_PATH = Path(__file__).parent / "grammar.lark"
_parser = Lark(
    _GRAMMAR_PATH.read_text(),
    parser="earley",
    propagate_positions=True,
)


def parse_flow(source: str) -> Flow:
    """Parse Flowstate DSL source text into a Flow AST.

    Raises FlowParseError on syntax errors with line/column information.
    """
    try:
        tree = _parser.parse(source)
    except UnexpectedInput as e:
        line = getattr(e, "line", None)
        column = getattr(e, "column", None)
        # Lark may use negative values for unknown positions
        if line is not None and line < 0:
            line = None
        if column is not None and column < 0:
            column = None
        raise FlowParseError(str(e), line=line, column=column) from e
    transformer = _FlowTransformer()
    try:
        return transformer.transform(tree)
    except VisitError as e:
        if isinstance(e.orig_exc, FlowParseError):
            raise e.orig_exc from e
        raise FlowParseError(str(e)) from e


def _parse_duration(token: str) -> int:
    """Convert a duration string like '30s', '5m', '2h' to integer seconds."""
    value, unit = int(token[:-1]), token[-1]
    return value * {"s": 1, "m": 60, "h": 3600}[unit]


def _strip_string(token: Token | str) -> str:
    """Strip quotes from a STRING or LONG_STRING token."""
    text = str(token)
    if text.startswith('"""') and text.endswith('"""'):
        return text[3:-3]
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text


def _meta_line(meta: Any) -> int:
    """Extract line from Lark meta, defaulting to 0."""
    return getattr(meta, "line", 0) or 0


def _meta_column(meta: Any) -> int:
    """Extract column from Lark meta, defaulting to 0."""
    return getattr(meta, "column", 0) or 0


class _FlowTransformer(Transformer[Token, Flow]):
    """Lark Transformer that builds a Flow AST from the parse tree."""

    # -- Literals and strings --

    def string(self, items: list[Token]) -> str:
        return _strip_string(items[0])

    def true_lit(self, _items: list[Token]) -> bool:
        return True

    def false_lit(self, _items: list[Token]) -> bool:
        return False

    def literal(self, items: list[str | float | bool]) -> str | float | bool:
        value = items[0]
        if isinstance(value, Token):
            if value.type == "STRING":
                return _strip_string(value)
            if value.type == "NUMBER":
                text = str(value)
                return int(text) if "." not in text else float(text)
        return value

    # -- Parameters --

    def param_decl(self, items: list[Token | str | float | bool]) -> Param:
        name = str(items[0])
        param_type = ParamType(str(items[1]))
        default = items[2] if len(items) > 2 else None
        return Param(name=name, type=param_type, default=default)

    # -- Node attributes (named alternatives) --

    def node_prompt(self, items: list[str]) -> tuple[str, str]:
        return ("prompt", items[0])

    def node_cwd(self, items: list[Token]) -> tuple[str, str]:
        return ("cwd", _strip_string(items[0]))

    def node_judge(self, items: list[Token]) -> tuple[str, bool]:
        return ("judge", str(items[0]) == "true")

    def node_body(self, items: list[tuple[str, str | bool]]) -> dict[str, str | bool]:
        result: dict[str, str | bool] = {}
        for key, value in items:
            result[key] = value
        return result

    # -- Nodes (with meta for line/column) --

    @v_args(meta=True)
    def entry_node(self, meta: Any, items: list[Token | dict[str, str | bool]]) -> Node:
        name = str(items[0])
        body: dict[str, str | bool] = (
            items[1] if len(items) > 1 and isinstance(items[1], dict) else {}
        )
        prompt = body.get("prompt", "")
        cwd = body.get("cwd")
        judge = body.get("judge")
        return Node(
            name=name,
            node_type=NodeType.ENTRY,
            prompt=str(prompt),
            cwd=str(cwd) if cwd is not None else None,
            judge=bool(judge) if judge is not None else None,
            line=_meta_line(meta),
            column=_meta_column(meta),
        )

    @v_args(meta=True)
    def task_node(self, meta: Any, items: list[Token | dict[str, str | bool]]) -> Node:
        name = str(items[0])
        body: dict[str, str | bool] = (
            items[1] if len(items) > 1 and isinstance(items[1], dict) else {}
        )
        prompt = body.get("prompt", "")
        cwd = body.get("cwd")
        judge = body.get("judge")
        return Node(
            name=name,
            node_type=NodeType.TASK,
            prompt=str(prompt),
            cwd=str(cwd) if cwd is not None else None,
            judge=bool(judge) if judge is not None else None,
            line=_meta_line(meta),
            column=_meta_column(meta),
        )

    @v_args(meta=True)
    def exit_node(self, meta: Any, items: list[Token | dict[str, str | bool]]) -> Node:
        name = str(items[0])
        body: dict[str, str | bool] = (
            items[1] if len(items) > 1 and isinstance(items[1], dict) else {}
        )
        prompt = body.get("prompt", "")
        cwd = body.get("cwd")
        judge = body.get("judge")
        return Node(
            name=name,
            node_type=NodeType.EXIT,
            prompt=str(prompt),
            cwd=str(cwd) if cwd is not None else None,
            judge=bool(judge) if judge is not None else None,
            line=_meta_line(meta),
            column=_meta_column(meta),
        )

    def node_decl(self, items: list[Node]) -> Node:
        return items[0]

    # -- Edge config attributes (named alternatives) --

    def edge_context(self, items: list[Token]) -> tuple[str, str]:
        return ("context", str(items[0]))

    def edge_delay(self, items: list[Token]) -> tuple[str, int]:
        return ("delay_seconds", _parse_duration(str(items[0])))

    def edge_schedule(self, items: list[Token]) -> tuple[str, str]:
        return ("schedule", _strip_string(items[0]))

    def edge_config(self, items: list[tuple[str, str | int]]) -> EdgeConfig:
        attrs: dict[str, str | int] = {}
        for key, value in items:
            attrs[key] = value
        return EdgeConfig(
            context=ContextMode(str(attrs["context"])) if "context" in attrs else None,
            delay_seconds=int(attrs["delay_seconds"]) if "delay_seconds" in attrs else None,
            schedule=str(attrs["schedule"]) if "schedule" in attrs else None,
        )

    # -- Name list for fork/join --

    def name_list(self, items: list[Token]) -> list[str]:
        return [str(item) for item in items]

    # -- Edges (with meta for line/column) --

    @v_args(meta=True)
    def simple_edge(self, meta: Any, items: list[Token | EdgeConfig | None]) -> Edge:
        source = str(items[0])
        target = str(items[1])
        config = items[2] if len(items) > 2 and isinstance(items[2], EdgeConfig) else EdgeConfig()
        return Edge(
            edge_type=EdgeType.UNCONDITIONAL,
            source=source,
            target=target,
            config=config,
            line=_meta_line(meta),
            column=_meta_column(meta),
        )

    @v_args(meta=True)
    def cond_edge(self, meta: Any, items: list[Token | str | EdgeConfig | None]) -> Edge:
        source = str(items[0])
        target = str(items[1])
        condition = str(items[2])
        config = items[3] if len(items) > 3 and isinstance(items[3], EdgeConfig) else EdgeConfig()
        return Edge(
            edge_type=EdgeType.CONDITIONAL,
            source=source,
            target=target,
            condition=condition,
            config=config,
            line=_meta_line(meta),
            column=_meta_column(meta),
        )

    @v_args(meta=True)
    def fork_edge(self, meta: Any, items: list[Token | list[str]]) -> Edge:
        source = str(items[0])
        targets = items[1]
        assert isinstance(targets, list)
        return Edge(
            edge_type=EdgeType.FORK,
            source=source,
            fork_targets=tuple(targets),
            line=_meta_line(meta),
            column=_meta_column(meta),
        )

    @v_args(meta=True)
    def join_edge(self, meta: Any, items: list[list[str] | Token]) -> Edge:
        sources = items[0]
        assert isinstance(sources, list)
        target = str(items[1])
        return Edge(
            edge_type=EdgeType.JOIN,
            join_sources=tuple(sources),
            target=target,
            line=_meta_line(meta),
            column=_meta_column(meta),
        )

    def edge_decl(self, items: list[Edge]) -> Edge:
        return items[0]

    # -- Flow attributes (named alternatives) --

    def flow_budget(self, items: list[Token]) -> tuple[str, int]:
        return ("budget_seconds", _parse_duration(str(items[0])))

    def flow_workspace(self, items: list[Token]) -> tuple[str, str]:
        return ("workspace", _strip_string(items[0]))

    def flow_on_error(self, items: list[Token]) -> tuple[str, str]:
        return ("on_error", str(items[0]))

    def flow_context(self, items: list[Token]) -> tuple[str, str]:
        return ("context", str(items[0]))

    def flow_schedule(self, items: list[Token]) -> tuple[str, str]:
        return ("schedule", _strip_string(items[0]))

    def flow_on_overlap(self, items: list[Token]) -> tuple[str, str]:
        return ("on_overlap", str(items[0]))

    def flow_skip_permissions(self, items: list[Token]) -> tuple[str, bool]:
        return ("skip_permissions", str(items[0]) == "true")

    def flow_judge(self, items: list[Token]) -> tuple[str, bool]:
        return ("judge", str(items[0]) == "true")

    def flow_worktree(self, items: list[Token]) -> tuple[str, bool]:
        return ("worktree", str(items[0]) == "true")

    # -- Flow body and declaration --

    def flow_stmt(self, items: list[object]) -> object:
        return items[0]

    def flow_body(self, items: list[object]) -> list[object]:
        return list(items)

    def flow_decl(self, items: list[Token | list[object]]) -> Flow:
        name = str(items[0])
        body_items = items[1] if isinstance(items[1], list) else []

        attrs: dict[str, str | int] = {}
        params: list[Param] = []
        nodes: dict[str, Node] = {}
        edges: list[Edge] = []

        for item in body_items:
            if isinstance(item, tuple):
                key, value = item
                attrs[key] = value  # type: ignore[assignment]
            elif isinstance(item, Param):
                params.append(item)
            elif isinstance(item, Node):
                nodes[item.name] = item
            elif isinstance(item, Edge):
                edges.append(item)

        # Validate required attributes
        if "budget_seconds" not in attrs:
            raise FlowParseError("missing required attribute 'budget'")
        if "on_error" not in attrs:
            raise FlowParseError("missing required attribute 'on_error'")
        if "context" not in attrs:
            raise FlowParseError("missing required attribute 'context'")

        # Handle on_overlap default
        on_overlap = (
            OverlapPolicy(str(attrs["on_overlap"])) if "on_overlap" in attrs else OverlapPolicy.SKIP
        )

        return Flow(
            name=name,
            budget_seconds=int(attrs["budget_seconds"]),
            on_error=ErrorPolicy(str(attrs["on_error"])),
            context=ContextMode(str(attrs["context"])),
            workspace=str(attrs["workspace"]) if "workspace" in attrs else None,
            schedule=str(attrs["schedule"]) if "schedule" in attrs else None,
            on_overlap=on_overlap,
            skip_permissions=bool(attrs.get("skip_permissions", False)),
            judge=bool(attrs.get("judge", False)),
            worktree=bool(attrs.get("worktree", True)),
            params=tuple(params),
            nodes=nodes,
            edges=tuple(edges),
        )

    def start(self, items: list[Flow]) -> Flow:
        return items[0]
