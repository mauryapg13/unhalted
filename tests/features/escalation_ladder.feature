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
    And it must NOT contain: penalty threats, legal threats, false urgency, invented discounts, or any commitment absent from merchant config
    And a lint failure blocks the send and logs the violating draft

  Scenario: AI hallucinates a discount — shell blocks it
    Given the AI drafts "pay today and get 20% off next month"
    And no such offer exists in merchant configuration
    When the compliance lint runs
    Then the message is blocked
    And regenerated without the offer
    And the hallucination event is logged for model QA
