import Definitions.Def_openaimath_011_totientfibersfromsmoothshiftedprimes



theorem openaimath_011_totientfibersfromsmoothshiftedprimes
    (hH : SmoothShiftedPrimesHypothesis) (ε : ℝ) (hε : 0 < ε) :
    Set.Infinite {n : ℕ | 0 < n ∧ ((totientFiberCard n : ℕ) : ℝ) > (n : ℝ) ^ (1 - ε)} := by sorry
