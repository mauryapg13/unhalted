# Mandate Lifecycle Recovery Agent — Gherkin Specification

**Architecture principle encoded in these specs:**
- **AI core** = perception & drafting (failure diagnosis, reply understanding, message drafting, intervention recommendation)
- **Deterministic shell** = action & stopping (retry caps, NPCI windows, contact hours, revocation stops, spend gates)
- Every AI output passes through a rules gate before touching money or a customer.

---

## Feature 1: Ambiguous Failure Diagnosis (AI Core)

```gherkin
Feature: Diagnose failed mandate debits from messy, inconsistent signals
  As a subscription merchant on Razorpay
  I want every failed debit classified by root cause with a confidence score
  So that the right recovery path is chosen instead of blind retries

  Background:
    Given the agent receives failure events with: PSP status, NPCI response code,
      issuer bank reason string, webhook payloads, and debit timing metadata
    And a diagnosis taxonomy exists:
      | class                        | recovery path                     |
      | recoverable-technical        | silent retry in allowed window    |
      | recoverable-balance          | customer-timed retry + nudge      |
      | notification-gap             | re-notify, then retry             |
      | mandate-state-broken         | re-authorization flow             |
      | customer-intent-revoked      | hard stop                         |
      | unknown                      | conservative hold + human review  |

  Scenario: Clean failure code maps deterministically
    Given a debit failed with NPCI code "U30" and issuer string "INSUFFICIENT BALANCE"
    When the agent diagnoses the failure
    Then the classification is "recoverable-balance" with confidence >= 0.95
    And no LLM call is required for this mapping
    And the diagnosis is logged with source "rules-table"

  Scenario: Conflicting signals require AI reconciliation
    Given the PSP status is "failed"
    But the issuer reason string is "TRANSACTION APPROVED - TIMEOUT AT SWITCH"
    When the agent diagnoses the failure
    Then it must first trigger a payment-status verification via API
    And it must NOT schedule any retry until verification completes
    And if verification shows the debit actually succeeded
    Then the case is closed as "false-failure" and no retry ever fires
    And the reconciliation reasoning is logged verbatim

  Scenario: Same code, different issuer semantics
    Given NPCI code "U67" means "debit timeout" for Issuer A
    But historically resolves as "mandate paused by customer" for Issuer B
    When a "U67" failure arrives from Issuer B
    Then the agent weights issuer-specific historical resolution data
    And classifies as "mandate-state-broken", not "recoverable-technical"
    And records which historical prior drove the classification

  Scenario: Novel failure pattern never seen before
    Given a failure arrives with reason string "DECLINED - AP RULE 7A"
    And this string matches no known taxonomy entry
    When the agent diagnoses the failure
    Then the classification is "unknown" regardless of LLM suggestion
    And the case enters a conservative hold (no retry, no customer contact)
    And it is queued for human review with the agent's best-guess hypothesis attached
    And if 5+ cases share this novel string within 24 hours
    Then a pattern alert is raised to the merchant ops dashboard

  Scenario: Time-correlated diagnosis (NPCI execution window decline)
    Given a debit failed at 10:42 with a generic "technical decline"
    And the debit time falls inside the NPCI restricted window (10:00-13:00)
    When the agent diagnoses the failure
    Then it must classify as "recoverable-technical" subtype "peak-window"
    And the retry recommendation must target the 13:00-17:00 window
    And the diagnosis must cite the window rule, not just the code

  Scenario: Pre-debit alert gap detected from metadata, not code
    Given a debit failed with a generic decline code
    And the notification-delivery log shows no confirmed 24-hour pre-debit alert
    When the agent diagnoses the failure
    Then the classification is "notification-gap"
    And the recovery path is: send fresh alert -> wait 24h -> retry
    And retrying without a confirmed alert is blocked by the shell

  Scenario Outline: Confidence threshold gates autonomy
    Given a diagnosis with confidence <confidence>
    When the agent decides the next action
    Then the action authority is "<authority>"

    Examples:
      | confidence | authority                          |
      | >= 0.90    | auto-execute recovery path         |
      | 0.70-0.89  | auto-execute, flag for sampling QA |
      | < 0.70     | hold and route to human review     |

  Scenario: Diagnosis is replayable
    Given any completed diagnosis
    When an auditor replays the case
    Then the stored record contains: all input signals, taxonomy version,
      model version, prompt hash, confidence, and chosen class
    And replaying the same inputs against the same versions yields the same class
```

---

## Feature 2: Retry Orchestration (Deterministic Shell)

```gherkin
Feature: Schedule retries under NPCI and RBI E-mandate 2026 constraints
  The shell enforces regulation as code; the AI may only recommend within it

  Background:
    Given NPCI restricted execution window is 10:00-13:00 IST for autopay debits
    And the RBI E-mandate Framework 2026 requires a delivered 24-hour pre-debit alert
    And the shell-enforced retry cap is 3 attempts per billing cycle

  Scenario: Retry lands only in permitted windows
    Given a "recoverable-technical" failure diagnosed at 10:42
    When the shell schedules the retry
    Then the retry time must be within 13:00-17:00 or after 21:30 same day
    And any AI recommendation of a time inside 10:00-13:00 is rejected and logged

  Scenario: Notification precondition is absolute
    Given a retry is due at 14:00
    But the fresh pre-debit alert delivery is unconfirmed
    When the scheduler evaluates the retry
    Then the retry is blocked
    And rescheduled to 24 hours after confirmed alert delivery
    And the block reason "ALERT_UNCONFIRMED" is logged

  Scenario: Balance-aware retry timing from AI insight
    Given a "recoverable-balance" diagnosis
    And the AI learned from a customer reply that salary credits on the 1st
    When the shell schedules the retry
    Then the retry targets the 2nd of the month, morning non-restricted window
    And the timing rationale references the customer-stated salary date

  Scenario: Retry cap cannot be overridden by any component
    Given 3 retries have failed this billing cycle
    When the AI recommends a 4th retry with confidence 0.99
    Then the shell rejects the recommendation
    And the case transitions to the escalation ladder
    And the rejection is logged as "STOP_RULE:RETRY_CAP"

  Scenario: Mandate expires mid-recovery
    Given a retry is scheduled for the 2nd
    But the mandate validity ends on the 1st
    When the scheduler runs its pre-flight check
    Then the retry is cancelled
    And the case reroutes to the re-authorization intervention
    And the customer message explains renewal, not payment failure

  Scenario: Revocation webhook cancels everything atomically
    Given 1 retry, 2 nudges, and 1 voice callback are pending for customer "C-101"
    When a mandate revocation webhook arrives
    Then all 4 pending actions are cancelled within one transaction
    And no partial execution is possible
    And the case closes as "customer-intent-revoked"
```

---

## Feature 3: Unstructured Reply Understanding (AI Core)

```gherkin
Feature: Parse free-text customer replies across languages and mixed intents
  As the recovery agent
  I want to extract intents, dates, conditions, and emotions from replies
  So that the recovery sequence adapts instead of spamming

  Background:
    Given customers reply on WhatsApp in English, Hindi, Hinglish, and regional languages
    And every parsed reply yields: intents[], entities{}, sentiment, confidence

  Scenario: Simple Hinglish promise-to-pay
    Given the customer replies "salary aane do, 2 tarikh ko pakka ho jayega"
    When the agent parses the reply
    Then intents include "promise-to-pay"
    And entities include payment_date = 2nd of next month
    And all nudges are suspended until the 3rd
    And the retry is realigned to the 2nd

  Scenario: Mixed intent — promise plus dispute plus set-off
    Given the customer replies
      "15th ko clear kar dunga, lekin pichhle month double debit hua tha, woh adjust karo pehle"
    When the agent parses the reply
    Then intents include "promise-to-pay" AND "dispute:double-debit" AND "set-off-request"
    And the dispute intent takes precedence over the promise
    Then all automated debits and nudges halt immediately
    And a double-debit verification task is created against transaction history
    And if the double debit is confirmed
    Then the refund/adjustment flow triggers BEFORE any recovery resumes
    And recovery resumes only after customer confirmation of the adjustment

  Scenario Outline: Opt-out detection across phrasings
    Given the customer replies "<reply>"
    When the agent parses the reply
    Then intents include "opt-out"
    And all future automated contact is suppressed within 60 seconds
    And the suppression is logged with the message reference

    Examples:
      | reply                                      |
      | mujhe dobara message mat karna             |
      | stop messaging me                          |
      | band karo ye sab                           |
      | STOP                                       |
      | why do you keep texting, leave me alone    |

  Scenario: Cancellation intent vs frustration — do not over-trigger
    Given the customer replies "ye service bekaar hai, paisa kat gaya par recharge nahi hua"
    When the agent parses the reply
    Then intents include "service-complaint", NOT "cancellation"
    And the case routes to the merchant's support flow with context attached
    And recovery pauses pending complaint resolution
    And the agent does NOT initiate mandate cancellation

  Scenario: Explicit cancellation intent is honored, not fought
    Given the customer replies "I want to cancel this subscription, stop charging me"
    When the agent parses the reply
    Then intents include "cancellation-request"
    And the reply includes the cancellation path (in-app / UPI app mandate section)
    And exactly one retention offer MAY be included only if merchant-configured
    And no further payment nudges are sent regardless of offer response

  Scenario: Conditional promise creates a tracked condition
    Given the customer replies "refund milte hi pay kar dunga"
    When the agent parses the reply
    Then a condition "refund-received" is attached to the promise
    And the agent checks refund status before any follow-up
    And if the refund is still pending on the promise date
    Then the follow-up acknowledges the pending refund instead of demanding payment

  Scenario: Ambiguous reply earns exactly one clarification
    Given the customer replies "hmm dekhta hu"
    When the agent parses with confidence < 0.70 on any actionable intent
    Then it sends one polite clarification with structured quick-reply options
    And if the clarification also goes unanswered or unparseable
    Then the case falls back to the default escalation timeline
    And no second clarification is ever sent

  Scenario: Distress language triggers human handoff
    Given the customer replies "bahut mushkil time chal raha hai, job chali gayi"
    When the agent parses the reply
    Then sentiment is "distress"
    And all automated recovery halts
    And the case is tagged "sensitive — human only"
    And any human follow-up template excludes urgency or penalty language

  Scenario: Low-confidence parse never drives money movement
    Given any parsed intent with confidence < 0.70
    When downstream actions are evaluated
    Then no retry timing change, no suppression release, and no escalation
      may be triggered from that parse
    And the raw message is preserved for human review

  Scenario: Parsed entities are validated before use
    Given the AI extracts payment_date = "31st February"
    When the shell validates entities
    Then the invalid date is rejected
    And the promise is recorded without a date
    And the follow-up asks the customer to confirm a date
```

---

## Feature 4: Escalation Ladder & Intervention Selection

```gherkin
Feature: Choose and sequence interventions matched to diagnosis and customer signals

  Background:
    Given the intervention ladder is:
      | rung | intervention                                   | cost   |
      | 1    | silent retry                                   | ₹0     |
      | 2    | WhatsApp nudge with one-tap pay link           | ~₹1    |
      | 3    | ₹1 re-authorization mandate flow               | ~₹2    |
      | 4    | AI voice call (Hinglish)                       | ~₹8    |
      | 5    | human agent callback                           | ~₹60   |

  Scenario: Diagnosis determines the entry rung, not always rung 1
    Given a "mandate-state-broken" diagnosis
    When the agent selects the entry intervention
    Then it enters at rung 3 (re-authorization) directly
    And silent retries are skipped because they cannot succeed

  Scenario: Economic gate on intervention spend
    Given a failed debit of ₹49 on a low-LTV subscription
    When the agent considers rung 4 (voice, ~₹8) after rung 2 failed
    Then the expected-value check compares recovery probability x ₹49 vs cost
    And if EV is negative, the ladder terminates early as "uneconomic"
    And the early termination is logged with the EV calculation

  Scenario: High-LTV customer justifies deeper ladder
    Given a failed debit of ₹499 with customer LTV ₹18,000 and tenure 3 years
    When rung 2 fails
    Then rung 4 may be authorized even at marginal single-cycle EV
    And the LTV rationale is recorded

  Scenario: Ladder respects customer channel preference learned from replies
    Given a prior reply said "call mat karo, message hi karna"
    When the ladder would normally escalate to rung 4 (voice)
    Then rung 4 is skipped permanently for this customer
    And the ladder proceeds rung 3 -> rung 5 with a no-call annotation

  Scenario: Re-authorization uses ₹1 mandate flow
    Given the intervention is rung 3
    When the re-authorization link is generated
    Then it uses the ₹1 authorization mandate mechanism
    And the message explains why re-authorization is needed in plain language
    And expiry of the link (72h) triggers exactly one reminder

  Scenario: Every outbound message passes compliance lint
    Given any AI-drafted customer message
    When the shell lints the draft
    Then it must contain the correct amount, merchant name, and opt-out hint
    And it must NOT contain: penalty threats, legal threats, false urgency,
      invented discounts, or any commitment absent from merchant config
    And a lint failure blocks the send and logs the violating draft

  Scenario: AI hallucinates a discount — shell blocks it
    Given the AI drafts "pay today and get 20% off next month"
    And no such offer exists in merchant configuration
    When the compliance lint runs
    Then the message is blocked
    And regenerated without the offer
    And the hallucination event is logged for model QA
```

---

## Feature 5: Stopping Rules (Deterministic, Non-Negotiable)

```gherkin
Feature: Hard stops that no AI recommendation can override

  Scenario Outline: Stop conditions and their scope
    Given an active recovery case
    When "<event>" occurs
    Then the shell executes "<stop_action>" within "<sla>"
    And logs "STOP_RULE:<code>"

    Examples:
      | event                                   | stop_action                        | sla    | code            |
      | mandate revoked via UPI app             | cancel all pending actions         | 60 sec | REVOKED         |
      | opt-out intent parsed (conf >= 0.70)    | suppress all automated contact     | 60 sec | OPT_OUT         |
      | dispute intent parsed                   | halt debits and nudges             | 60 sec | DISPUTE         |
      | distress sentiment detected             | halt automation, human-only tag    | 60 sec | DISTRESS        |
      | retry cap (3) reached                   | end retries, enter ladder          | n/a    | RETRY_CAP       |
      | ladder exhausted                        | close case as unrecovered          | n/a    | LADDER_END      |
      | chargeback raised on the mandate        | freeze all recovery on customer    | 5 min  | CHARGEBACK      |
      | merchant pauses recovery program        | freeze all cases for merchant      | 5 min  | MERCHANT_PAUSE  |
      | regulator/PSP advisory flag on mandate  | freeze case, human review          | 5 min  | REG_HOLD        |

  Scenario: Contact frequency ceiling across all channels
    Given 2 automated contacts were made to a customer this week
    When any component proposes a 3rd contact within the same week
    Then the shell blocks it
    And defers it to the next eligible day
    And multi-channel contacts (WhatsApp + voice) count against one shared ceiling

  Scenario: Contact hours apply to every channel including retries of messages
    Given a WhatsApp nudge send fails at 18:58 and auto-retry queues it
    When the message retry would fire at 19:05
    Then the send is deferred to 08:00 next day
    And the deferral is logged

  Scenario: Opt-out survives system restarts and model changes
    Given customer "C-303" opted out 6 months ago
    When a new recovery case opens for a fresh failed debit
    Then automated contact remains suppressed
    And only rung-1 silent retries and rung-5 human contact are permitted
```

---

## Feature 6: Audit Trail & Batch Measurement

```gherkin
Feature: Every decision is attributable, replayable, and aggregated into ₹ recovered

  Scenario: Decision record completeness
    Given any agent decision (diagnosis, schedule, message, escalation, stop)
    When the decision is committed
    Then the audit record contains:
      | field                                                    |
      | case_id, customer_ref (pseudonymized), timestamp         |
      | decision_type and chosen action                          |
      | all input signals and their sources                      |
      | rules fired (with rule version)                          |
      | model name, version, prompt hash, confidence (if AI)     |
      | human actor id (if human gate)                           |
      | outcome linkage (payment id / stop code / next state)    |

  Scenario: PII minimization in the audit trail
    Given audit records are written
    Then raw customer messages are stored encrypted with access logging
    And phone numbers and VPAs are tokenized in analytical views
    And retention follows DPDP purpose-limitation configuration

  Scenario: Batch recovery measurement
    Given a batch of 500 failed debits worth ₹2,40,000 processed this month
    When the measurement job runs
    Then the report shows, per diagnosis class:
      | metric                                          |
      | count, ₹ at risk, ₹ recovered, recovery rate    |
      | recovery by rung (which intervention converted) |
      | mean time-to-recovery                           |
      | stop-rule counts by code                        |
      | intervention spend and net recovery ROI         |
      | human-review queue volume and resolution rate   |
    And "false-failure" recoveries are reported separately, not as agent wins

  Scenario: Attribution honesty — control group
    Given the merchant enables measurement mode
    Then a configurable holdout (e.g., 10%) of recoverable cases receives
      only baseline behavior (single blind retry)
    And reported lift = agent cohort recovery minus holdout recovery
    And the report never claims gross recovery as net lift

  Scenario: Case replay for compliance review
    Given a regulator or merchant requests review of case "CASE-8812"
    When the replay tool runs
    Then it reconstructs the full timeline: every signal in, decision out,
      message sent (verbatim), customer reply (verbatim), and stop events
    And the timeline exports as a single signed document
```

---

## Feature 7: Human Gates & Degradation Behavior

```gherkin
Feature: The agent degrades safely when its components fail

  Scenario: LLM service outage
    Given the reply-parsing model is unreachable
    When customer replies arrive
    Then replies queue for parsing with no data loss
    And no reply-dependent action fires on stale assumptions
    And deterministic flows (scheduled retries with confirmed alerts) continue
    And an ops alert fires if the queue exceeds 30 minutes

  Scenario: Human review queue overflow
    Given the human review queue exceeds its staffed capacity threshold
    When new low-confidence cases arrive
    Then the agent widens the conservative-hold band automatically
    And recovery SLAs are re-forecast and surfaced to the merchant

  Scenario: Human overrides an AI diagnosis
    Given a reviewer reclassifies a case from "unknown" to "recoverable-balance"
    When the override is saved
    Then the case resumes on the corrected path
    And the override is captured as labeled training data
    And weekly override rates per diagnosis class feed the QA dashboard

  Scenario: Statutory or irreversible actions always require a human
    Given any action that is irreversible or legally significant
      (mandate cancellation on customer's behalf, refund issuance above threshold,
       escalation to formal notice)
    When the agent reaches such an action
    Then it prepares the artifact and waits for explicit human approval
    And auto-approval by timeout is prohibited
```
