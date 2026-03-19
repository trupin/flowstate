class FlowParseError(Exception):
    """Raised when the DSL source text cannot be parsed into a valid AST."""

    def __init__(self, message: str, line: int | None = None, column: int | None = None) -> None:
        self.line = line
        self.column = column
        loc = ""
        if line is not None:
            loc = f" at line {line}"
            if column is not None:
                loc += f", column {column}"
        super().__init__(f"Parse error{loc}: {message}")
