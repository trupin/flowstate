#!/usr/bin/env bash
# connect-wrapper.sh -- pipe a command into an existing OpenShell sandbox.
#
# Usage: connect-wrapper.sh <sandbox-name> <agent-command>
#
# Sends the agent command through ``openshell sandbox connect`` so that
# the command runs inside the named persistent sandbox with provider auth.
# Remaining stdin is forwarded to the connected session (for ACP messages).
#
# FLOWSTATE_* env vars are forwarded via explicit export statements
# prepended to the command, since the sandbox doesn't inherit host env.
#
# Terminal escape codes from the sandbox connection (Landlock warnings,
# bracketed paste mode) are handled by the ACP library which auto-skips
# non-JSON lines in the JSON-RPC stream.
#
# NOTE: We use `openshell sandbox connect` (not ssh -T) because it
# provides provider auth routing that ssh doesn't get.
set -euo pipefail

SANDBOX_NAME="$1"
AGENT_CMD="$2"

# Build env export prefix for FLOWSTATE_* variables
ENV_EXPORTS=""
for var in FLOWSTATE_SERVER_URL FLOWSTATE_RUN_ID FLOWSTATE_TASK_ID; do
  val="${!var:-}"
  if [ -n "$val" ]; then
    ENV_EXPORTS="${ENV_EXPORTS}export ${var}='${val}'; "
  fi
done

{
  printf 'stty raw -echo 2>/dev/null; %sexec %s\n' "$ENV_EXPORTS" "$AGENT_CMD"
  sleep 2  # Wait for stty + exec to take effect
  cat      # Forward remaining stdin (ACP messages)
} | exec openshell sandbox connect "$SANDBOX_NAME"
