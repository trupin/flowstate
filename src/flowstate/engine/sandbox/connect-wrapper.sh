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
# The single Landlock warning line at connection start is auto-skipped
# by the ACP library's JSON parser (logs and continues).
set -euo pipefail

SANDBOX_NAME="$1"
AGENT_CMD="$2"

exec ssh -T \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o GlobalKnownHostsFile=/dev/null \
  -o LogLevel=ERROR \
  -o "ProxyCommand=openshell ssh-proxy --gateway-name openshell --name ${SANDBOX_NAME}" \
  "sandbox@openshell-${SANDBOX_NAME}" \
  "exec ${AGENT_CMD}"
