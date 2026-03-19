---
description: Validate a .flow file — parse it and run the type checker
user_invocable: true
---

Validate the given `.flow` file by parsing it and running the type checker.

If no file path is provided in the user's message, ask for one.

Run:
```bash
uv run python -c "
import sys
from flowstate.dsl.parser import parse_flow
from flowstate.dsl.type_checker import check_flow

with open(sys.argv[1]) as f:
    source = f.read()

try:
    flow = parse_flow(source)
except Exception as e:
    print(f'Parse error: {e}')
    sys.exit(1)

errors = check_flow(flow)
if errors:
    for err in errors:
        print(err)
    sys.exit(1)
else:
    print(f'OK — flow \"{flow.name}\" is valid ({len(flow.nodes)} nodes, {len(flow.edges)} edges)')
" "$FLOW_FILE"
```

Replace `$FLOW_FILE` with the path provided by the user.

Report the result clearly: either the validation errors with their rule IDs, or a success message.
