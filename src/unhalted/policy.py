"""Every policy number this system enforces, read from one file.

`config/policy.yaml` is the source; this module parses it once at import
into typed values and gets out of the way. It knows nothing about
`DiagnosisClass` or `Rung` — those belong to `scheduler.py` and `ladder.py`,
which map this module's plain string keys onto their own enums themselves.
Keeping this module ignorant of domain types is what keeps it free to load
before them, with no circular import to work around.

A value in `config/policy.yaml` that this module cannot parse fails at
import time, loudly, rather than silently taking a default nobody chose —
the same reasoning that makes an unparseable retry date get refused rather
than guessed at.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from datetime import time, timedelta

import yaml

from unhalted import config

RUPEE = 100


class BadPolicy(RuntimeError):
    """The policy file exists but does not say what it needs to."""


def _parse_time(raw: str) -> time:
    try:
        hh, mm = raw.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError) as e:
        raise BadPolicy(f"not a time (expected 'HH:MM'): {raw!r}") from e


def _parse_duration(raw: str) -> timedelta:
    """'30m', '2h', '1d' — a number and one unit letter. Nothing fancier;
    every duration this policy needs is a whole number of one unit."""
    units = {"m": "minutes", "h": "hours", "d": "days"}
    if not raw or raw[-1] not in units:
        raise BadPolicy(f"not a duration (expected a number then m/h/d): {raw!r}")
    try:
        amount = int(raw[:-1])
    except ValueError as e:
        raise BadPolicy(f"not a duration (expected a number then m/h/d): {raw!r}") from e
    return timedelta(**{units[raw[-1]]: amount})


def _rupees_to_paise(rupees: object) -> int:
    if not isinstance(rupees, (int, float)):
        raise BadPolicy(f"not a rupee amount: {rupees!r}")
    return round(rupees * RUPEE)


@dataclass(frozen=True)
class Policy:
    """Everything `config/policy.yaml` says, parsed and typed."""

    version: str

    npci_restricted_bands: tuple[tuple[time, time], ...]
    npci_rule_version: str

    contact_open: time
    contact_close: time
    #: Automated messages one customer may receive per rolling week, across
    #: every case and channel.
    contact_max_per_week: int
    contact_week: timedelta

    retry_cap: int
    #: Keyed by the plain DiagnosisClass *value* string, e.g.
    #: "recoverable-technical" — not the enum, so this module never imports it.
    backoff_raw: dict[str, tuple[timedelta, ...]]
    default_backoff: timedelta
    #: How long a balance case waits for the customer to name a date before
    #: falling back to the blind backoff schedule above.
    reply_grace: timedelta

    confidence_auto_execute: float
    confidence_sampled_qa: float

    reply_acts_on_money: float
    reply_protective: float
    reply_cancellation: float
    reply_rule_version: str

    ladder_rule_version: str
    #: Keyed by plain rung slug, e.g. "reauthorisation" — see `ladder.py`'s
    #: own mapping from slug to `Rung`.
    ladder_rung_costs_paise: dict[str, int]

    limit_rule_version: str
    frictionless_upi_paise: int
    frictionless_upi_bfsi_paise: int
    upi_mandate_max_paise: int
    card_recurring_max_paise: int
    emandate_max_paise: int


def _require(d: dict, *path: str) -> object:
    cur: object = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            raise BadPolicy(f"missing required key: {'.'.join(path)}")
        cur = cur[key]
    return cur


def load(path: pathlib.Path | None = None) -> Policy:
    """Read and validate `config/policy.yaml` (or `UNHALTED_POLICY`)."""
    p = path or pathlib.Path(config.policy_path())
    if not p.exists():
        raise BadPolicy(f"no policy file at {p}")

    raw = yaml.safe_load(p.read_text())
    if not isinstance(raw, dict):
        raise BadPolicy(f"{p} did not parse to a mapping")

    bands_raw = _require(raw, "npci", "restricted_bands")
    if not isinstance(bands_raw, list) or not bands_raw:
        raise BadPolicy("npci.restricted_bands must be a non-empty list")
    bands = tuple(
        (_parse_time(a), _parse_time(b)) for a, b in bands_raw
    )

    backoff_raw = _require(raw, "retries", "backoff")
    if not isinstance(backoff_raw, dict):
        raise BadPolicy("retries.backoff must be a mapping")
    backoff = {
        klass: tuple(_parse_duration(d) for d in durations)
        for klass, durations in backoff_raw.items()
    }

    rung_costs_raw = _require(raw, "ladder", "rung_costs_rupees")
    if not isinstance(rung_costs_raw, dict):
        raise BadPolicy("ladder.rung_costs_rupees must be a mapping")
    rung_costs_paise = {
        slug: _rupees_to_paise(rupees) for slug, rupees in rung_costs_raw.items()
    }

    return Policy(
        version=str(_require(raw, "version")),
        npci_restricted_bands=bands,
        npci_rule_version=str(_require(raw, "npci", "rule_version")),
        contact_open=_parse_time(str(_require(raw, "contact", "open"))),
        contact_close=_parse_time(str(_require(raw, "contact", "close"))),
        contact_max_per_week=int(_require(raw, "contact", "max_per_week")),
        contact_week=_parse_duration(str(_require(raw, "contact", "week"))),
        retry_cap=int(_require(raw, "retries", "cap")),
        backoff_raw=backoff,
        default_backoff=_parse_duration(str(_require(raw, "retries", "default_backoff"))),
        reply_grace=_parse_duration(str(_require(raw, "retries", "reply_grace"))),
        confidence_auto_execute=float(_require(raw, "confidence", "auto_execute")),
        confidence_sampled_qa=float(_require(raw, "confidence", "auto_execute_sampled_qa")),
        reply_acts_on_money=float(_require(raw, "reply_policy", "acts_on_money")),
        reply_protective=float(_require(raw, "reply_policy", "protective")),
        reply_cancellation=float(_require(raw, "reply_policy", "cancellation")),
        reply_rule_version=str(_require(raw, "reply_policy", "rule_version")),
        ladder_rule_version=str(_require(raw, "ladder", "rule_version")),
        ladder_rung_costs_paise=rung_costs_paise,
        limit_rule_version=str(_require(raw, "limits", "rule_version")),
        frictionless_upi_paise=_rupees_to_paise(_require(raw, "limits", "frictionless_upi_rupees")),
        frictionless_upi_bfsi_paise=_rupees_to_paise(
            _require(raw, "limits", "frictionless_upi_bfsi_rupees")
        ),
        upi_mandate_max_paise=_rupees_to_paise(_require(raw, "limits", "upi_mandate_max_rupees")),
        card_recurring_max_paise=_rupees_to_paise(
            _require(raw, "limits", "card_recurring_max_rupees")
        ),
        emandate_max_paise=_rupees_to_paise(_require(raw, "limits", "emandate_max_rupees")),
    )


#: Loaded once at import, the same pattern `config.py` uses for the
#: environment. A module that needs a fresh read after editing the file by
#: hand calls `load()` again itself; nothing here watches the file change.
POLICY = load()
