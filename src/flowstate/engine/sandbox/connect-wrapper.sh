#!/usr/bin/env bash
# connect-wrapper.sh -- pipe a command into an existing OpenShell sandbox.
#
# Usage: connect-wrapper.sh <sandbox-name> <agent-command>
#
# Sends the agent command through ``openshell sandbox connect`` so that
# the command runs inside the named persistent sandbox.  Remaining stdin
# is forwarded to the connected session (for ACP communication).
set -euo pipefail

SANDBOX_NAME="$1"
AGENT_CMD="$2"

{
  printf 'stty raw -echo 2>/dev/null; exec %s\n' "$AGENT_CMD"
  sleep 2  # Wait for stty + exec to take effect
  cat      # Forward remaining stdin (ACP messages)
} | exec openshell sandbox connect "$SANDBOX_NAME"
