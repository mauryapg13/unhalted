#!/usr/bin/env bash
# Post N failed payments with the same error shape, over the real signed webhook.
#
#   ./scripts/demo_inject.sh                                  # 3x token_not_found
#   ./scripts/demo_inject.sh --count 5                        # 5 of them
#   ./scripts/demo_inject.sh --reason risk_check_failed       # a different gap
#
# This is not a shortcut past the pipeline. Each payment is signed with the real
# `RAZORPAY_WEBHOOK_SECRET` and posted to the same endpoint Razorpay posts to, so
# the signature check, the idempotency check, the normaliser and the taxonomy all
# run exactly as they do in production. The only thing supplied here is the
# payload, which is what Razorpay would otherwise supply.
#
# It exists because `scripts/inject.py` covers the five *documented* card
# scenarios, and demonstrating a taxonomy gap needs a reason that is deliberately
# not one of them.
#
# Each payment gets its own id, event id and amount, so they open separate cases
# rather than being recognised as one payment redelivered.

set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

COUNT=3
REASON="token_not_found"
SOURCE="issuer_bank"
PORT="${DEMO_PORT:-8123}"
FIXTURE="tests/fixtures/razorpay/payment_failed_netbanking.json"

while [ $# -gt 0 ]; do
  case "$1" in
    --count)  COUNT="$2"; shift 2 ;;
    --reason) REASON="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --port)   PORT="$2";   shift 2 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [ ! -f .env ]; then
  echo "no .env — RAZORPAY_WEBHOOK_SECRET has to come from somewhere" >&2
  exit 1
fi
SECRET=$(grep '^RAZORPAY_WEBHOOK_SECRET=' .env | cut -d= -f2-)
if [ -z "$SECRET" ]; then
  echo "RAZORPAY_WEBHOOK_SECRET is not set in .env" >&2
  exit 1
fi

# Refuse rather than post into whatever else is listening. A stale server from an
# earlier run will happily accept these and write them to *its* database, and the
# cases then appear to have vanished.
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $PORT is already in use — stop that server first, or pass --port" >&2
  exit 1
fi

DB="${UNHALTED_DB:-unhalted.db}"
echo "  posting $COUNT x $REASON (source=$SOURCE) to $DB"

RAZORPAY_WEBHOOK_SECRET="$SECRET" UNHALTED_DB="$DB" \
  uv run uvicorn unhalted.ingest.webhooks:app --port "$PORT" --log-level warning &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

until curl -s -o /dev/null "localhost:$PORT/health" 2>/dev/null; do sleep 0.5; done

for i in $(seq 1 "$COUNT"); do
  BODY=$(python3 - "$i" "$REASON" "$SOURCE" "$FIXTURE" <<'PY'
import json, sys
i, reason, source, fixture = sys.argv[1:5]
d = json.load(open(fixture))
p = d["payload"]["payment"]["entity"]
p["error_reason"] = reason
p["error_source"] = source
p["id"] = f"pay_{reason.upper()}{i}"
# A different amount each time, so the cases are visibly distinct on screen
# rather than three identical rows nobody can tell apart.
p["amount"] = (int(i) + 4) * 10000
if "customer_id" in p:
    p["customer_id"] = f"cust_{reason}{i}"
print(json.dumps(d))
PY
)
  SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/.*= //')
  curl -s -X POST "localhost:$PORT/webhooks/razorpay" \
    -H "x-razorpay-signature: $SIG" \
    -H "x-razorpay-event-id: evt_${REASON}_${i}" \
    -d "$BODY" | sed 's/^/  /'
  echo
done

echo
echo "  next:  uv run unhalted queue"
echo "         uv run python scripts/propose_taxonomy_rule.py"
