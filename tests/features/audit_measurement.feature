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
    Then a configurable holdout (e.g., 10%) of recoverable cases receives only baseline behavior (single blind retry)
    And reported lift = agent cohort recovery minus holdout recovery
    And the report never claims gross recovery as net lift

  Scenario: Case replay for compliance review
    Given a regulator or merchant requests review of case "CASE-8812"
    When the replay tool runs
    Then it reconstructs the full timeline: every signal in, decision out, message sent (verbatim), customer reply (verbatim), and stop events
    And the timeline exports as a single signed document
