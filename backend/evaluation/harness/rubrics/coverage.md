# Rubric: coverage, stage 2 — are the reference facts covered? (GraphRAG-Bench `metrics/coverage.py`)

This is GraphRAG-Bench's own fact-coverage step (FACT_COVERAGE_PROMPT), done by the judge instead
of an API model. Each item has a question, a list of **reference facts**, and one **response**. You
do not know which system wrote the response; do not try to guess.

Task (verbatim from the benchmark): *For each factual statement from the reference, determine if
it's covered in the response.* A fact is covered (1) if the response states it or something with
the same meaning; otherwise 0 (missing, contradicted, or only vaguely hinted at).

Example (from the benchmark):
- Response: "Seasons are caused by Earth's tilted axis"
- Reference facts: ["Seasonal changes result from Earth's axial tilt", "The axial tilt causes
  different hemispheres to receive varying sunlight"]
- Classifications: [1, 0]

For each item output exactly one line, with one 0/1 per reference fact, in the same order:

    {"bid": "<bid>", "attributed": [1, 0, ...], "reason": "<one sentence>"}

The item's coverage score is the mean of `attributed`. Output one line per item, in file order,
nothing else. Never edit grades of an earlier batch.
