Feature: Diagnose failed mandate debits from messy, inconsistent signals
  As a subscription merchant on Razorpay
  I want every failed debit classified by root cause with a confidence score
  So that the right recovery path is chosen instead of blind retries

  Background:
    Given the agent receives failure events with: PSP status, NPCI response code, issuer bank reason string, webhook payloads, and debit timing metadata
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
    And the debit time falls inside an NPCI restricted window (10:00-13:00)
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
    Then the stored record contains: all input signals, taxonomy version, model version, prompt hash, confidence, and chosen class
    And replaying the same inputs against the same versions yields the same class
