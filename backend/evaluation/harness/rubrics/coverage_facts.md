# Rubric: coverage, stage 1 — extract reference facts (GraphRAG-Bench `metrics/coverage.py`)

This is GraphRAG-Bench's own fact-extraction step (FACT_EXTRACTION_PROMPT), done by the judge
instead of an API model. Each line of the batch file has a question and its **reference answer**
(no system output is involved).

Task (verbatim from the benchmark): *Extract distinct factual statements from the reference answer
that could be independently verified.*

Example (from the benchmark):
- Question: "What causes seasons?"
- Reference: "Seasonal changes result from Earth's axial tilt. This tilt causes different
  hemispheres to receive varying sunlight."
- Facts: ["Seasonal changes result from Earth's axial tilt", "The axial tilt causes different
  hemispheres to receive varying sunlight"]

For each item output exactly one line:

    {"fid": "<the item's fid>", "facts": ["fact 1", "fact 2", ...]}

Keep each fact short and self-contained; cover everything the reference states; add nothing that it
doesn't state. Output one line per item, in file order, nothing else.
