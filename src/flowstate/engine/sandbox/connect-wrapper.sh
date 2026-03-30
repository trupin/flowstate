#!/usr/bin/env bash
# connect-wrapper.sh -- run a command inside an existing OpenShell sandbox.
#
# Usage: connect-wrapper.sh <sandbox-name> <agent-command>
#
# Uses ssh -T (no TTY allocation) with openshell ssh-proxy as the
# ProxyCommand to get a clean binary stdio channel.  This avoids the
# terminal escape code contamination that openshell sandbox connect
# produces (bracketed paste mode, command echo, stty artifacts).
#
# FLOWSTATE_* env vars are forwarded to the remote command via explicit
# export statements, since SSH does not forward environment variables.
# The command runs via a login shell (bash -l) so that the sandbox user's
# stored credentials (e.g., claude login) are available.
#
# The single Landlock warning line at connection start is auto-skipped
# by the ACP library's JSON parser (logs and continues).
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

exec ssh -T \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o GlobalKnownHostsFile=/dev/null \
  -o LogLevel=ERROR \
  -o "ProxyCommand=openshell ssh-proxy --gateway-name openshell --name ${SANDBOX_NAME}" \
  "sandbox@openshell-${SANDBOX_NAME}" \
  "bash -l -c '${ENV_EXPORTS}exec ${AGENT_CMD}'"
