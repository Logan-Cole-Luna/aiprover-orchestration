import Definitions.Def_openaimath_332_metricmarkovcotypel1

open MarkovCotype

theorem openaimath_332_metricmarkovcotypel1
    (n t : ℕ) (ht : 1 ≤ t)
    (π : Fin n → ℝ) (hπ : IsProbVec π)
    (A : Matrix (Fin n) (Fin n) ℝ) (hA : IsStochastic A)
    (hrev : IsReversible π A)
    (x : Fin n → L1) :
    ∃ y : Fin n → L1,
      ∑ i, π i * ‖x i - y i‖ ^ 2
        + (t : ℝ) * ∑ i, ∑ j, π i * A i j * ‖y i - y j‖ ^ 2
      ≤ 3024 * ∑ i, ∑ j,
          π i * ((1 / (t : ℝ)) * ∑ s ∈ Finset.Icc 1 t, (A ^ s) i j)
            * ‖x i - x j‖ ^ 2 := by sorry
