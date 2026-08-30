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
    Given the customer replies "15th ko clear kar dunga, lekin pichhle month double debit hua tha, woh adjust karo pehle"
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
    Then no retry timing change, no suppression release, and no escalation may be triggered from that parse
    And the raw message is preserved for human review

  Scenario: Parsed entities are validated before use
    Given the AI extracts payment_date = "31st February"
    When the shell validates entities
    Then the invalid date is rejected
    And the promise is recorded without a date
    And the follow-up asks the customer to confirm a date
