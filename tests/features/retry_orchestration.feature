Feature: Schedule retries under NPCI and RBI E-mandate 2026 constraints
  The shell enforces regulation as code; the AI may only recommend within it

  Background:
    Given NPCI restricted execution windows are 10:00-13:00 and 17:00-21:30 IST for autopay debits, leaving before 10:00, 13:00-17:00 and after 21:30 permitted
    And the RBI E-mandate Framework 2026 requires a delivered 24-hour pre-debit alert
    And a subsequent charge is initiated 25 hours after that alert is sent
    And the shell-enforced retry cap is 3 attempts per billing cycle

  Scenario: Retry lands only in permitted windows
    Given a "recoverable-technical" failure diagnosed at 10:42
    When the shell schedules the retry
    Then the retry time must be within 13:00-17:00 or after 21:30 same day
    And any AI recommendation of a time inside 10:00-13:00 or 17:00-21:30 is rejected and logged

  Scenario: Notification precondition is absolute
    Given a retry is due at 14:00
    But the fresh pre-debit alert delivery is unconfirmed
    When the scheduler evaluates the retry
    Then the retry is blocked
    And rescheduled to 25 hours after confirmed alert delivery
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
