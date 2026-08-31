#!/usr/bin/env bash
# Launches Razorpay's official remote MCP server with credentials read from
# .env at run time, so no secret is ever written into a committed config file.
#
# Refuses to start on a live key. These tools can create refunds, revoke
# mandates and trigger settlements. An agent is never handed that reach.

set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$root/.env" ]; then
  echo "razorpay-mcp: no .env at $root" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
. "$root/.env"
set +a

: "${RAZORPAY_KEY_ID:?razorpay-mcp: RAZORPAY_KEY_ID is not set}"
: "${RAZORPAY_KEY_SECRET:?razorpay-mcp: RAZORPAY_KEY_SECRET is not set}"

case "$RAZORPAY_KEY_ID" in
  rzp_test_*) ;;
  *)
    echo "razorpay-mcp: refusing to start — key is not rzp_test_*." >&2
    echo "  These tools can create refunds, revoke mandates and trigger" >&2
    echo "  settlements. They are never pointed at live credentials." >&2
    exit 1
    ;;
esac

auth=$(printf '%s:%s' "$RAZORPAY_KEY_ID" "$RAZORPAY_KEY_SECRET" | base64 | tr -d '\n')

exec npx -y mcp-remote https://mcp.razorpay.com/mcp \
  --header "Authorization:Basic ${auth}"
