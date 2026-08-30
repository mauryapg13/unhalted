"""Verify every external dependency before the build starts.

Reads credentials from the environment or a local .env. Never prints secret
values. Refuses to touch anything but Razorpay test mode.

    python3 scripts/preflight.py
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

RAZORPAY = "https://api.razorpay.com/v1"
OPENROUTER_DEFAULT = "https://openrouter.ai/api/v1"

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"
results: list[tuple[str, str, str]] = []


def record(status: str, name: str, note: str = "") -> None:
    results.append((status, name, note))


def load_dotenv(path: str = ".env") -> None:
    f = pathlib.Path(path)
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def get(url: str, headers: dict[str, str]) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read() or b"{}"
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body.decode(errors="replace")[:200]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def describe(payload: dict) -> str:
    err = payload.get("error")
    if isinstance(err, dict):
        return err.get("description") or err.get("code") or ""
    return str(err or payload.get("raw") or "")[:90]


def check_razorpay() -> None:
    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")

    if not key_id or not key_secret:
        record(FAIL, "Razorpay credentials", "RAZORPAY_KEY_ID / _KEY_SECRET not set")
        return
    if key_id.startswith("rzp_live_"):
        record(FAIL, "Razorpay key mode", "LIVE key detected — refusing to continue")
        return
    if not key_id.startswith("rzp_test_"):
        record(WARN, "Razorpay key mode", f"unexpected prefix '{key_id[:9]}'")
    else:
        record(PASS, "Razorpay key mode", "test mode")

    token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
    headers = {"Authorization": f"Basic {token}"}

    status, payload = get(f"{RAZORPAY}/payments?count=1", headers)
    if status == 200:
        record(PASS, "Razorpay auth", "credentials accepted")
    else:
        record(FAIL, "Razorpay auth", f"HTTP {status} {describe(payload)}")
        return

    for label, path in [
        ("Orders API", "orders?count=1"),
        ("Customers API", "customers?count=1"),
        ("Plans API (Subscriptions)", "plans?count=1"),
        ("Subscriptions API", "subscriptions?count=1"),
        ("Tokens API (recurring)", "payments?count=1&expand[]=token"),
    ]:
        status, payload = get(f"{RAZORPAY}/{path}", headers)
        if status == 200:
            record(PASS, label, "available in test mode")
        elif status in (400, 401, 403):
            record(WARN, label, f"HTTP {status} {describe(payload)}")
        else:
            record(FAIL, label, f"HTTP {status} {describe(payload)}")


def check_model() -> None:
    """Verify the AI core's endpoint, and that the configured model actually answers."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    model = os.environ.get("UNHALTED_MODEL", "")
    base = os.environ.get("OPENROUTER_BASE_URL", OPENROUTER_DEFAULT).rstrip("/")

    if not key:
        record(FAIL, "Model credentials", "OPENROUTER_API_KEY not set")
        return
    if not model:
        record(FAIL, "Model selection", "UNHALTED_MODEL not set")
        return

    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": 'Reply with only {"ok": true}'}],
            "max_tokens": 400,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        record(FAIL, "Model endpoint", f"HTTP {e.code} {e.read().decode(errors='replace')[:90]}")
        return
    except Exception as e:  # noqa: BLE001
        record(FAIL, "Model endpoint", str(e)[:90])
        return

    content = (body.get("choices") or [{}])[0].get("message", {}).get("content")
    if not content:
        record(FAIL, "Model response", "empty content returned")
        return

    cost = (body.get("usage") or {}).get("cost")
    served_by = body.get("provider") or "unknown provider"
    record(PASS, "Model endpoint", f"{body.get('model')} via {served_by}")
    if cost is not None:
        record(PASS, "Per-call cost reporting", f"available (${cost:.8f} for this probe)")
    else:
        record(WARN, "Per-call cost reporting", "not returned; model spend must be estimated")


def check_webhook_secret() -> None:
    if os.environ.get("RAZORPAY_WEBHOOK_SECRET"):
        record(PASS, "Webhook secret", "set")
    else:
        record(SKIP, "Webhook secret", "set this after creating the webhook in the dashboard")


def main() -> int:
    load_dotenv()
    check_razorpay()
    check_model()
    check_webhook_secret()

    width = max(len(name) for _, name, _ in results)
    print()
    for status, name, note in results:
        print(f"  {status:<4}  {name:<{width}}  {note}")
    print()

    failures = sum(1 for s, _, _ in results if s == FAIL)
    warnings = sum(1 for s, _, _ in results if s == WARN)
    print(f"  {len(results)} checks, {failures} failed, {warnings} warned")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
