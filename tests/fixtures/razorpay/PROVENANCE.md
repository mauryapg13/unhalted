# Fixture provenance

Every payload here must be traceable to Razorpay. None is written by hand — a fixture invented
by whoever also wrote the parser proves only that the two agree with each other.

There are two kinds, and the difference matters.

## `captured/` — real payments

Real test-mode payments that failed at Razorpay's hosted checkout, arrived here over a real
webhook with a signature verified against the registered secret, and were fetched back through
their API. Each file records its payment id and capture date.

| Payment | Method | Error | Captured |
|---|---|---|---|
| `pay_TWtdZSPcdnn9sv` | card | `payment_failed` / `gateway` | 2026-09-02 |
| `pay_TWtk6oYP2Rnqvq` | card | `payment_failed` / `gateway` | 2026-09-02 |
| `pay_TX7bxkAG4ZrWNO` | card | `payment_failed` / `gateway` | 2026-09-02 |

**What is real:** the payment object, the error fields, the webhook delivery and its signature,
and the API they were read from — all produced by Razorpay's own systems.

**What is simulated:** the cause. In test mode no bank declines anything; Razorpay's mock
checkout decides the failure. That is their simulation, not ours, and it is the closest thing to
a real decline obtainable without live credentials and a customer with an empty account.

## The four in this directory — Razorpay's published examples

`payment_failed_{netbanking,card,wallets,upi}.json`, extracted programmatically from
`razorpay/markdown-docs` → `webhooks/payments.md`, section "Payment Failed", on 2026-08-31.

`payment_link_paid.json`, extracted the same way from `webhooks/payment-links.md`, section
"Payment Link Paid" (Standard), on 2026-09-04. Used to test the other side of the loop
`shell/paylink.py` opens: `reference_id` carries the case id back, and closing on it needs a
payload with a real one to close against.

Their field names, shapes and values are authoritative. The payments they describe never existed.
They cover methods and error reasons this account cannot produce.

## Why the captured set is small

Two constraints, both verified rather than assumed:

1. There is no API-only path to a failed payment on this account — server-to-server card creation
   returns 403 and the S2S UPI endpoint returns 404. Producing one needs a human at the checkout.
2. Razorpay's error-scenario cards do not yield their documented reasons on this account through
   **either** checkout surface. Tested on the hosted payment-link page (2026-08-31) and on
   Standard Checkout, the widget those cards are documented for (2026-09-01,
   `pay_TX7bxkAG4ZrWNO`). Both return a generic `payment_failed` / `gateway` whatever card is
   used. Issue #8, closed as answered.

So the captured set proves the pipeline runs on genuine Razorpay output. Breadth of error reasons
comes from their published examples, labelled as such.
