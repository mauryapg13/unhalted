# Capturing real fixtures

Every payload the test suite runs on should come off Razorpay's infrastructure. A fixture
written by whoever also wrote the parser proves only that the two agree with each other.

There is no API-only path to a failed payment on this account — server-to-server card creation
returns 403 and the S2S UPI endpoint returns 404 — so producing one needs a human at the hosted
checkout. This is that procedure. It takes about ten minutes and is done once.

## 1. Register the webhook

Razorpay Dashboard -> **Settings -> Webhooks -> Add New Webhook**

| | |
|---|---|
| **Webhook URL** | `https://<your-tunnel>.ngrok-free.dev/webhooks/razorpay` |
| **Secret** | whatever `RAZORPAY_WEBHOOK_SECRET` is set to in your `.env` |
| **Active events** | `payment.failed`, `payment_link.paid`, and `payment.captured` if offered |

Read the secret out of `.env` rather than from here — it is a credential, and a credential written
into a tracked file is a credential published. An earlier version of this table had the real one in
it. The URL is an ngrok tunnel and **changes every time ngrok restarts**; if the tunnel is
restarted, update the dashboard.

```bash
grep '^RAZORPAY_WEBHOOK_SECRET=' .env
```

## 2. Pay each link with the card it names

Each link's notes record which of Razorpay's published error-scenario cards to use. Open the
link, choose **Card**, enter the card number, any future expiry, any CVV, and on the mock bank
page choose **Failure**.

The card number is what determines the error. Use the one the link names, or the fixture will
be attributed to the wrong reason.



| Expected error reason | Card to enter | Link |
|---|---|---|
| `card_declined` | `4100 2800 0006 0003` | https://rzp.io/rzp/8dXGAFQ |
| `authentication_failed` | `4100 2800 0000 0009` | https://rzp.io/rzp/8yQvuY37 |
| `gateway_technical_error` | `4100 2800 0002 0007` | https://rzp.io/rzp/YR4W0nW |
| `payment_timed_out` | `4100 2800 0009 0000` | https://rzp.io/rzp/KGORbd5V |
| `insufficient_fund` | `4100 2800 0008 0001` | https://rzp.io/rzp/Vfral6i |

## 3. Capture

```bash
uv run python scripts/capture_fixtures.py
```

Writes one file per real failed payment to `tests/fixtures/razorpay/captured/`, each recording
the payment id, when it was captured, and the error fields Razorpay returned.

## What this proves

Each captured payment arrived over a real webhook with a signature verified against the secret
above, opened a real case, and was diagnosed by the taxonomy generated from Razorpay's own
documentation. Nothing in that path was written to make a test pass.
