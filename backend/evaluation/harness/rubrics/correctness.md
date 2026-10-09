# Rubric: reference-based correctness (EVALUATION_PLAN.md §6, mode 1)

You are grading answers to questions against gold answers. You see only the question, the gold
answer(s) and one answer. You do not know which system produced the answer; do not try to guess.

For each item in the batch file (one JSON object per line), output exactly one line:

    {"bid": "<the item's bid>", "score": <1 | 0.5 | 0>, "reason": "<one sentence>"}

Scores:

- **1** — the answer has the same meaning as one of the gold answers. Aliases, paraphrases,
  different but equivalent units or date formats, and extra harmless words are fine.
- **0.5** — partly correct: some of the required parts are present but not all (e.g. one of two
  requested items), or the right answer together with a wrong extra claim.
- **0** — wrong, contradicts the gold answer, answers a different question, is empty, or says the
  information is missing/insufficient when a gold answer exists.

Special case: if the item has `"null_query": true`, the correct behaviour is to say the
information is missing (e.g. "Insufficient information"): that scores **1**, and any specific
answer scores **0**.

Rules:
- Grade each item independently, in file order. Output one line per item, nothing else.
- Judge meaning, not style or length. Do not reward longer answers.
- Do not use outside knowledge to override the gold answer: the gold answer is correct by definition.
- Never edit grades of an earlier batch.
