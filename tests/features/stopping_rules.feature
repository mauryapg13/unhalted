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
