# What broke

A running log of development failures, what caused them, and what changed as a result.
Written as things break, not reconstructed afterwards.

---

### The specification was not valid Gherkin
**Date:** 2026-08-31

**What happened:** The first test written against the spec — one that only parses the feature files
and checks they contain scenarios — failed on six of the seven files immediately.

**Why:** The specification was written as a design document before it was ever executed, and long
steps were wrapped across several indented lines for readability. That reads well in Markdown, but
Gherkin steps must be a single line unless they use a docstring or a data table. Nobody had ever run
the parser over it, so nobody knew. I then made it worse: the correction adding NPCI's second
restricted band followed the same wrapped style and introduced a seventh broken file.

**What changed:** Wrapped continuation lines are now joined into single steps, verified by
`tests/test_specification.py`, which runs in CI. Two lessons kept:

1. A specification nobody has executed is a document, not a contract. It was treated as settled for
   several days while being unparseable.
2. The first test written should be the one that validates the foundation everything else assumes —
   not a test of the first feature built. Nine tests that parse the spec caught a problem that would
   otherwise have surfaced halfway through implementing step definitions, with far less clarity
   about the cause.

---

### The model endpoint returned nothing, twice, for no visible reason
**Date:** 2026-08-31

**What happened:** The first Hinglish extraction probe crashed on its second case with
`'NoneType' object has no attribute 'strip'`. The HTTP status was 200, the response was
well-formed, and `choices[0].message.content` was `null`.

**Why:** Two causes, found by dumping the raw response instead of guessing. The first occurrence was
self-inflicted — `max_tokens: 32`, too small for the model to produce anything. The second was not:
OpenRouter routes a single model across providers, and the same request was served by DeepInfra on
one call and Z.AI on another. Null content appears to be routing variance. It did not recur across
six subsequent calls.

**What changed:** Empty content is now treated as an expected outcome rather than an exception. The
AI core retries on empty, and a persistent null degrades to a failed parse that the shell routes to
human review — which is the behaviour the specification already required for a low-confidence parse.
An intermittent fault that appears once in seven calls is exactly the kind that survives to
production, so it is handled now rather than after it appears in a demo.
