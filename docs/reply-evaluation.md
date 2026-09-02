# Reply parser evaluation

Generated 2026-09-02 11:26 UTC · prompt `da4005e4eb27` ·
model `z-ai/glm-5.3-flash` · 68 replies

## Provenance of the corpus

13 replies are taken verbatim from the project's Gherkin specification,
written before any of this code existed and therefore not tuned to what the model can do. The
remaining 55 were authored for this evaluation, along with all the expected labels.

That is a real limit on what these numbers mean: they measure agreement with one person's reading
of what these replies intend. There is no public corpus of real Hinglish payment replies and this
test account has no customers, so no better source exists yet. Replies typed by someone who has
not seen the prompt are the only genuinely held-out data available, and are worth more than
another fifty authored ones.

Where the labels and the model disagreed on dates, the labels were wrong three times out of four.

## Reliability, which is a different question from accuracy

A parse failure is not the model misreading a reply. It is the endpoint returning a 200 with no
content — OpenRouter routes one model across providers and they do not all behave alike. The
parser retries 3 times and then gives up, marking the reply failed rather than
inferring anything from silence.

Counting those as missed intents would blame the model for an infrastructure fault and hide the
real problem, so the accuracy below is measured **only over replies that parsed**.

| | |
|---|---|
| Replies | 68 |
| Parsed successfully | 67 (99%) |
| Failed after 3 attempts | 1 |
| Needed at least one retry | 4 |
| Model calls made | 74 for 68 replies |

A failure is operationally safe — the reply is preserved and queued for a human, and nothing fires
on a guess. It is still a reply the agent did not handle, and at this rate it is the largest single
weakness in the parser.

## Precision and recall by intent

Over the 67 replies that parsed.

| Intent | TP | FP | FN | Precision | Recall |
|---|---:|---:|---:|---:|---:|
| `cancellation-request` | 5 | 4 | 0 | 0.56 | 1.00 |
| `channel-preference` | 5 | 0 | 0 | 1.00 | 1.00 |
| `dispute` | 9 | 4 | 0 | 0.69 | 1.00 |
| `distress` | 7 | 1 | 0 | 0.88 | 1.00 |
| `opt-out` | 12 | 0 | 0 | 1.00 | 1.00 |
| `promise-to-pay` | 17 | 3 | 0 | 0.85 | 1.00 |
| `service-complaint` | 8 | 0 | 0 | 1.00 | 1.00 |
| `set-off-request` | 4 | 2 | 0 | 0.67 | 1.00 |
| `unknown` | 5 | 0 | 1 | 1.00 | 0.83 |

**Recall** answers "did we miss any of these?" **Precision** answers "when we said yes, were we
right?"

## The numbers that matter, and they are on opposite sides

A single headline figure would hide the important half, because the two directions of error cost
differently.

- **Recall on `opt-out` and `distress`.** Missing one means messaging somebody who asked to be left
  alone, or dunning somebody who has lost their job. Money does not compensate for either.
- **Precision on `cancellation-request`.** A false positive cancels a paying customer who was
  merely annoyed. The parser over-detects this, and `shell/replies.py` refuses to act below 0.85
  confidence — so the model's precision here is not the system's behaviour. No reply on the
  corpus's forbid-list was acted on as a cancellation.

## Failures

**Parse failures:** 1

- pp-05: model returned empty content

**Forbidden intents detected:** none.

**Date mismatches:** 1

- spec-02: wanted 2026-09-15, got 2026-10-15

**Promises that named a date and changed nothing:** none.

