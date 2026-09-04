"""What a parsed reply is allowed to change.

The model reads; this decides. Two things live here rather than in the prompt,
because both are policy and policy belongs where it can be tested:

**Precedence.** A reply carrying both a promise and a dispute is a dispute. The
customer says they will pay *and* that you took their money twice; acting on the
promise and ignoring the dispute is how a complaint becomes a chargeback.

**Thresholds, and they are deliberately asymmetric.** The two directions of
error cost wildly different amounts:

- Missing an opt-out means messaging someone who said stop. That is a compliance
  failure. Missing distress means dunning someone who has lost their job.
  Both err toward acting, so they fire on weaker evidence.
- Falsely detecting a cancellation means cancelling a paying customer who was
  merely annoyed. That errs toward doing nothing, so it needs strong evidence.

A single threshold would be wrong in one direction or the other, whichever
number was chosen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from unhalted.models import Intent, ParsedReply, Sentiment
from unhalted.policy import POLICY

#: Anything that moves money or releases a suppression. The specification's
#: bar. Read from config/policy.yaml — see unhalted.policy.
ACTS_ON_MONEY = POLICY.reply_acts_on_money

#: Protective stops. Lower, because stopping wrongly costs a delayed recovery
#: and failing to stop costs a compliance breach or a person in hardship.
PROTECTIVE = POLICY.reply_protective

#: Cancelling a subscription is close to irreversible from the customer's side,
#: and frustration reads a lot like intent to leave. Needs strong evidence.
CANCELLATION = POLICY.reply_cancellation

REPLY_RULE_VERSION = POLICY.reply_rule_version


@dataclass(frozen=True)
class DateCheck:
    value: date | None
    accepted: bool
    reason: str


@dataclass
class ReplyOutcome:
    """What the shell will do about a reply, and why."""

    stop_code: str | None = None
    realign_to: date | None = None
    suspend_nudges_until: date | None = None
    route_to_support: bool = False
    channel_refused: str | None = None
    needs_human: bool = False
    rules_fired: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    rule_version: str = REPLY_RULE_VERSION

    @property
    def changes_money(self) -> bool:
        return self.realign_to is not None


def validate_date(
    raw: str | None,
    *,
    today: date,
    mandate_expires: date | None = None,
) -> DateCheck:
    """Turn the model's proposed date into one, or refuse it with a reason.

    The model is asked for `YYYY-MM-DD` and sometimes produces something that is
    not a date at all — the specification's example is "31st February". A type
    that happens to parse is not validation; this is.
    """
    if not raw:
        return DateCheck(None, False, "no date given")

    try:
        # A calendar date, not an instant — no timezone applies to "the 2nd".
        value = date.fromisoformat(raw.strip())
    except ValueError:
        return DateCheck(None, False, f"{raw!r} is not a real date")

    if value < today:
        return DateCheck(None, False, f"{value} is in the past")

    if mandate_expires and value > mandate_expires:
        return DateCheck(
            None, False, f"{value} is after the mandate expires on {mandate_expires}"
        )

    return DateCheck(value, True, f"{value} accepted")


def decide(
    parsed: ParsedReply,
    *,
    today: date,
    mandate_expires: date | None = None,
) -> ReplyOutcome:
    """Decide what a reply changes. Ordered most protective first."""
    out = ReplyOutcome()

    if parsed.failed:
        out.needs_human = True
        out.rules_fired.append("PARSE_FAILED")
        out.reasons.append(
            f"the reply could not be read ({parsed.failure_reason}); "
            "it is preserved and queued rather than guessed at"
        )
        return out

    # 1. Distress. Halts everything and goes to a person.
    if parsed.has(Intent.DISTRESS, at_least=PROTECTIVE) or parsed.sentiment is Sentiment.DISTRESS:
        out.stop_code = "DISTRESS"
        out.needs_human = True
        out.rules_fired.append("STOP_RULE:DISTRESS")
        out.reasons.append("hardship described; automated recovery is halted")
        return out

    # 2. Dispute outranks any promise in the same reply.
    if parsed.has(Intent.DISPUTE, at_least=PROTECTIVE):
        out.stop_code = "DISPUTE"
        # Halting is not enough. A dispute is a factual question about the
        # customer's money, and nothing here can answer it: the verification is
        # against transaction history, and any resulting refund needs human
        # approval. A dispute that halts without flagging anyone abandons the
        # case silently, which is worse than not halting at all.
        out.needs_human = True
        out.rules_fired.append("STOP_RULE:DISPUTE")
        out.reasons.append(
            "a disputed charge halts debits and nudges, and goes to a person: "
            "the claim must be verified against transaction history before recovery resumes"
        )
        if parsed.has(Intent.PROMISE_TO_PAY):
            out.rules_fired.append("PRECEDENCE:DISPUTE_OVER_PROMISE")
            out.reasons.append(
                "the reply also promises payment; the dispute is resolved first"
            )
        return out

    # 3. Opt-out.
    if parsed.has(Intent.OPT_OUT, at_least=PROTECTIVE):
        out.stop_code = "OPT_OUT"
        out.rules_fired.append("STOP_RULE:OPT_OUT")
        out.reasons.append("they asked not to be contacted")
        return out

    # 4. Cancellation, but only on strong evidence.
    if parsed.has(Intent.CANCELLATION_REQUEST, at_least=CANCELLATION):
        out.needs_human = True
        out.rules_fired.append("CANCELLATION_REQUESTED")
        out.reasons.append(
            "cancellation is requested; it is prepared for a human rather than executed"
        )
        return out

    # 5. A complaint is not a cancellation. Pause, route, do not cancel.
    if parsed.has(Intent.SERVICE_COMPLAINT, at_least=PROTECTIVE):
        out.route_to_support = True
        out.rules_fired.append("ROUTED_TO_SUPPORT")
        out.reasons.append(
            "a service complaint pauses recovery and goes to support; "
            "frustration is not a request to leave"
        )
        if parsed.has(Intent.CANCELLATION_REQUEST):
            out.reasons.append(
                f"cancellation was also detected but below the "
                f"{CANCELLATION} threshold, so it is not acted on"
            )
        return out

    # 6. A promise moves money, so it needs the higher bar.
    if parsed.has(Intent.PROMISE_TO_PAY, at_least=ACTS_ON_MONEY):
        check = validate_date(parsed.payment_date_raw, today=today, mandate_expires=mandate_expires)
        if check.accepted and check.value:
            out.realign_to = check.value
            out.suspend_nudges_until = check.value
            out.rules_fired.append("PROMISE_ACCEPTED")
            out.reasons.append(f"retry realigned to {check.value}; nudges suspended until then")
        else:
            out.rules_fired.append("PROMISE_WITHOUT_USABLE_DATE")
            out.reasons.append(
                f"a promise was made but the date was refused ({check.reason}); "
                "the promise is recorded and the follow-up asks them to confirm a date"
            )
    elif parsed.has(Intent.PROMISE_TO_PAY):
        out.rules_fired.append("BELOW_ACTION_THRESHOLD")
        out.reasons.append(
            f"a promise was read at below {ACTS_ON_MONEY} confidence, "
            "so it changes no timing"
        )

    # 7. A stated channel preference is honoured permanently.
    if parsed.has(Intent.CHANNEL_PREFERENCE, at_least=PROTECTIVE):
        out.channel_refused = "voice"
        out.rules_fired.append("CHANNEL_PREFERENCE_RECORDED")
        out.reasons.append("a stated contact preference is honoured for this customer")

    if not out.rules_fired:
        out.needs_human = True
        out.rules_fired.append("NOTHING_ACTIONABLE")
        out.reasons.append("nothing actionable was read; the raw reply is kept for review")

    return out
