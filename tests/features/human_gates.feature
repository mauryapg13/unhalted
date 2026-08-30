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
    Given any action that is irreversible or legally significant (mandate cancellation on customer's behalf, refund issuance above threshold, escalation to formal notice)
    When the agent reaches such an action
    Then it prepares the artifact and waits for explicit human approval
    And auto-approval by timeout is prohibited
