#!/usr/bin/env bash
# Drives a failed payment through the running service, end to end.
#
# This is not a demo path through the code — it posts to the same endpoints
# Razorpay posts to, with a real HMAC signature, and reads the case back through
# the same API anyone else would use.
#
#   ./scripts/demo.sh
#
# Note on timing: the NPCI window rule is real and reads the real clock. Inside
# a restricted band (10:00-13:00 or 17:00-21:30 IST) you will see the shell move
# the retry and log WINDOW_VIOLATION. Outside one, the retry is simply accepted.
# Both are correct; only the first is interesting to watch.

set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PORT="${DEMO_PORT:-8111}"
SECRET="demo_secret_not_a_real_one"
DB="$(mktemp -t unhalted-demo-XXXXXX).db"
FIXTURE="tests/fixtures/razorpay/payment_failed_netbanking.json"

cleanup() {
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true
  rm -f "$DB"
}
trap cleanup EXIT

echo "starting unhalted on port $PORT"
RAZORPAY_WEBHOOK_SECRET="$SECRET" UNHALTED_DB="$DB" \
  uv run uvicorn unhalted.ingest.webhooks:app --port "$PORT" --log-level warning &
SERVER_PID=$!

if ! curl -s --retry-connrefused --retry 30 --retry-delay 1 -o /dev/null \
     "localhost:$PORT/health"; then
  echo "server did not start" >&2
  exit 1
fi

BODY=$(python3 -c "import json,sys;print(json.dumps(json.load(open(sys.argv[1]))))" "$FIXTURE")
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/.*= //')

echo
echo "clock: $(TZ=Asia/Kolkata date '+%H:%M IST')"
echo

echo "1. a forged webhook — anyone can claim a payment failed"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "localhost:$PORT/webhooks/razorpay" \
  -H "x-razorpay-signature: forged" -d "$BODY")
echo "   HTTP $code, no case created"
echo

echo "2. the real webhook, signed by Razorpay's secret"
RESP=$(curl -s -X POST "localhost:$PORT/webhooks/razorpay" \
  -H "x-razorpay-signature: $SIG" -H "x-razorpay-event-id: evt_demo" -d "$BODY")
echo "   $RESP"
CASE=$(printf '%s' "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin)['case_id'])")
echo

echo "3. Razorpay redelivers it — expected behaviour, not a bug"
curl -s -X POST "localhost:$PORT/webhooks/razorpay" \
  -H "x-razorpay-signature: $SIG" -H "x-razorpay-event-id: evt_demo" -d "$BODY" \
  | sed 's/^/   /'
echo

echo "4. the case"
curl -s "localhost:$PORT/cases/$CASE" | python3 -c "
import sys, json
d = json.load(sys.stdin)
c, dg, sig = d['case'], d['diagnosis'], d['signals'][0]
print(f\"   {c['id']}   Rs {c['amount_paise']/100:.0f}   customer {c['customer_ref']}\")
print(f\"   from Razorpay: reason={sig['error_reason']} source={sig['error_source']} step={sig['error_step']}\")
print()
print(f\"   diagnosis  {dg['klass']}  confidence {dg['confidence']}  via {dg['source']}\")
print(f\"              {dg['reasoning']}\")
print()
print('   timeline')
for r in d['timeline']:
    print(f\"     {r['decision_type']:<10} {r['action']}\")
    for f in r['rules_fired']:
        print(f\"                rule: {f}\")
"
