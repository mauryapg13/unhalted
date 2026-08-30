#!/usr/bin/env bash
# Enforces this repository's git workflow on the agent, not on the agent's memory.
#
# 1. Refuses any commit made directly on `main`. Work reaches the trunk by pull
#    request from a short-lived branch; see CHECKPOINTS.md.
# 2. Warns when a commit carries no CHANGELOG.md change, which is what `/wr`
#    exists to keep current.
#
# Reads the Claude Code PreToolUse payload on stdin. Always exits 0; the
# decision is carried in the JSON it prints.

set -uo pipefail

payload=$(cat)
command=$(printf '%s' "$payload" | jq -r '.tool_input.command // ""' 2>/dev/null)

# Not a commit: allow silently.
if ! printf '%s' "$command" | grep -Eq '(^|[;&|[:space:]])git[[:space:]]+commit([[:space:]]|$)'; then
  exit 0
fi

branch=$(git symbolic-ref --short HEAD 2>/dev/null || git rev-parse --abbrev-ref HEAD 2>/dev/null)

if [ "$branch" = "main" ]; then
  jq -nc '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: "Refused: this repository does not take commits directly on main. Work reaches the trunk by pull request from a short-lived branch (CHECKPOINTS.md). Create a branch first: git checkout -b feat/<name>"
    }
  }'
  exit 0
fi

# On a feature branch. Warn if the changelog has not moved.
if git diff --quiet HEAD -- CHANGELOG.md 2>/dev/null; then
  jq -nc --arg b "$branch" '{
    systemMessage: ("No CHANGELOG.md change on " + $b + ". /wr adds the entry — use it rather than committing without one.")
  }'
fi
exit 0
