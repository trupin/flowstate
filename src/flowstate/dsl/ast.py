from dataclasses import dataclass, field
from enum import StrEnum


class NodeType(StrEnum):
    ENTRY = "entry"
    TASK = "task"
    EXIT = "exit"


class EdgeType(StrEnum):
    UNCONDITIONAL = "unconditional"
    CONDITIONAL = "conditional"
    FORK = "fork"
    JOIN = "join"
    FILE = "file"
    AWAIT = "await"


class ContextMode(StrEnum):
    HANDOFF = "handoff"
    SESSION = "session"
    NONE = "none"


class ErrorPolicy(StrEnum):
    PAUSE = "pause"
    ABORT = "abort"
    SKIP = "skip"


class OverlapPolicy(StrEnum):
    SKIP = "skip"
    QUEUE = "queue"
    PARALLEL = "parallel"


@dataclass(frozen=True)
class TaskTypeField:
    """A single field in a task type input or output declaration."""

    name: str
    type: str  # "string", "number", "bool"
    default: str | float | bool | None = None


@dataclass(frozen=True)
class Node:
    name: str
    node_type: NodeType
    prompt: str
    cwd: str | None = None
    judge: bool | None = None
    line: int = 0
    column: int = 0


@dataclass(frozen=True)
class EdgeConfig:
    context: ContextMode | None = None
    delay_seconds: int | None = None
    schedule: str | None = None


@dataclass(frozen=True)
class Edge:
    edge_type: EdgeType
    source: str | None = None
    target: str | None = None
    fork_targets: tuple[str, ...] | None = None
    join_sources: tuple[str, ...] | None = None
    condition: str | None = None
    config: EdgeConfig = field(default_factory=EdgeConfig)
    line: int = 0
    column: int = 0


@dataclass(frozen=True)
class Flow:
    name: str
    budget_seconds: int
    on_error: ErrorPolicy
    context: ContextMode
    workspace: str | None = None
    schedule: str | None = None
    on_overlap: OverlapPolicy = OverlapPolicy.SKIP
    skip_permissions: bool = False
    judge: bool = False
    worktree: bool = True
    input_fields: tuple[TaskTypeField, ...] = ()
    output_fields: tuple[TaskTypeField, ...] = ()
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: tuple[Edge, ...] = ()
