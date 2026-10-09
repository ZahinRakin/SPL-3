# Rubric: pairwise preference (EVALUATION_PLAN.md §6, mode 2)

You compare two answers (A and B) to the same question about a novel. You see the question, the
benchmark's reference answer, its evidence sentences, and the two answers. You do not know which
systems produced them; do not try to guess. The same pair may appear again later with A and B
swapped; judge every item on its own.

Criteria, in this order of importance:

1. **Comprehensiveness** — covers the points in the reference answer and evidence.
2. **Faithfulness** — states nothing that contradicts the reference/evidence or invents facts.
3. **Directness** — answers the question that was asked without padding. Between two answers that
   are equally comprehensive and faithful, prefer the **shorter, more focused** one. Never prefer
   an answer just because it is longer.

For each item, output exactly one line:

    {"bid": "<bid>", "winner": "A" | "B" | "tie", "reason": "<one sentence>"}

Use "tie" when neither answer is clearly better on the criteria above. Output one line per item,
in file order, nothing else. Never edit grades of an earlier batch.
