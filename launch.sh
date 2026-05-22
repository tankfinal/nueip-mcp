#!/bin/bash
# Wrapper for nueip-mcp that pulls NUEIP_PASSWORD from macOS Keychain
# instead of expecting it in plain text in ~/.claude.json.
#
# Prereq (one-time):
#   security add-generic-password -s nueip -a "$USER" -U -w
#   (interactive prompt for the password — note `-w` is lowercase, at end)
#
# Then in ~/.claude.json mcpServers.nueip.command = path to this script,
# and DROP NUEIP_PASSWORD from the env block.
#
# This wrapper also performs a best-effort daily update check against
# the upstream repo; if your local checkout is behind origin/main, it
# prints a one-shot reminder to stderr. The check is silent on every
# failure path (no network, no .git, etc.) and never delays MCP startup
# by more than ~3 seconds.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Update check (best-effort, never fails the launch) -------------------
(
  set +e  # don't let any single command failure inside abort the subshell

  cd "$REPO_DIR" || exit 0
  [[ -d .git ]] || exit 0

  cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/nueip-mcp"
  mkdir -p "$cache_dir" 2>/dev/null
  cache_file="$cache_dir/last-update-check"

  now=$(date +%s)
  last=0
  [[ -f "$cache_file" ]] && last=$(cat "$cache_file" 2>/dev/null)
  [[ -z "$last" ]] && last=0

  # Throttle: skip if checked in the last 24h
  (( now - last < 86400 )) && exit 0

  # Touch cache before doing the work — failed checks don't retry every spawn
  echo "$now" > "$cache_file" 2>/dev/null

  # Fetch with a 3-second timeout (macOS has no GNU `timeout`)
  ( git fetch --quiet origin main 2>/dev/null ) &
  fpid=$!
  for _ in 1 2 3; do
    kill -0 "$fpid" 2>/dev/null || break
    sleep 1
  done
  kill "$fpid" 2>/dev/null
  wait "$fpid" 2>/dev/null

  local_sha=$(git rev-parse HEAD 2>/dev/null)
  remote_sha=$(git rev-parse origin/main 2>/dev/null)

  [[ -z "$local_sha" || -z "$remote_sha" ]] && exit 0
  [[ "$local_sha" == "$remote_sha" ]] && exit 0

  # Only notify if local is strictly behind (ancestor of remote).
  # Skips notification when local has unrelated commits / diverged
  # branches — that's a developer concern, not a "needs update".
  git merge-base --is-ancestor "$local_sha" "$remote_sha" 2>/dev/null || exit 0

  {
    echo ""
    echo "═══ nueip-mcp 有新版本可用 ═══"
    echo "  local:  ${local_sha:0:8}"
    echo "  remote: ${remote_sha:0:8}"
    echo "  更新：cd \"$REPO_DIR\" && git pull && uv sync"
    echo ""
  } >&2
) || true

# --- Password from Keychain -----------------------------------------------
PASS=$(security find-generic-password -s nueip -a "$USER" -w 2>/dev/null || true)

if [[ -z "$PASS" ]]; then
  echo "nueip launch.sh: NUEIP password not found in Keychain (service=nueip, account=$USER)" >&2
  echo "Add it first: security add-generic-password -s nueip -a \"\$USER\" -U -w" >&2
  exit 1
fi

export NUEIP_PASSWORD="$PASS"
unset PASS

exec uv --directory "$REPO_DIR" run nueip-mcp
