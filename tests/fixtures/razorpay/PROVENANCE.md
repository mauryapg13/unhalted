# Fixture provenance

Every payload in this directory must be traceable to Razorpay. None are written by hand — a
fixture invented by whoever also wrote the parser proves only that the two agree with each
other.

## Current contents

`payment_failed_{netbanking,card,wallets,upi}.json`

- **Source:** Razorpay's published webhook documentation,
  `razorpay/markdown-docs` → `webhooks/payments.md`, section "Payment Failed".
- **Extracted:** 2026-08-31, programmatically from the document rather than retyped.
- **What they are:** Razorpay's own example payloads. Their field names, shapes and values
  are authoritative; the payments they describe never existed.
- **What they are not:** captured from a real payment.

## What replaces them at C4

Real payment objects captured from test mode, produced with Razorpay's error-scenario test
cards through the hosted checkout. Each will record its payment id, capture date and the card
that produced it. At that point these become a secondary corpus for field shapes Razorpay
documents but our account cannot generate.

## Why they are not real yet

There is no API-only path to a failed payment on this account — server-to-server card creation
returns 403 and the S2S UPI endpoint returns 404 (verified 2026-08-31, recorded in
CHECKPOINTS.md). A failed payment requires the hosted checkout, which needs a human.
