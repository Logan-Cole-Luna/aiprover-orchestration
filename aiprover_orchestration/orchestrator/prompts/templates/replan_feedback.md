
A previous sketch was attempted. Solvers proved some lemmas and failed on others. Revise the sketch: keep proved lemmas verbatim if they are still useful (they will not be re-proved), and replace or split the failed ones into easier steps. Each failed lemma lists the solvers' last attempts, most complete first, with Lean's errors: they show where the proof stalled. A step left as `sorry` or a `have` that does not compile can become a lemma of its own, and a statement that resists proof may need to be restated. A failed lemma marked FALSE was refuted in Lean: do not state it again, and plan around what its refutation shows; if it shows the theorem itself to be false, refute the theorem. If no proof is known, keep attempting one: change the approach or the decomposition rather than isolating the difficulty in a lemma equivalent to the theorem.

<previous_lemmas>
{lemmas}
</previous_lemmas>
<previous_main_proof>
{main_proof}
</previous_main_proof>
<proved_lemmas>
{proved}
</proved_lemmas>
<failed_lemmas>
{failed}
</failed_lemmas>
<learned_clauses>
{clauses}
</learned_clauses>
The learned clauses hold for the whole run: do not state a lemma they record as refuted, and do not choose a route or encoding they record as failed.
