import Definitions.Def_openaimath_164_hindmandistinctexpressions



theorem openaimath_164_hindmandistinctexpressions (hT : HindmanFSFP.Thm11)
    (r m : ℕ) (hr : 1 ≤ r) (hm : 1 ≤ m) (χ : ℕ → Fin r) :
    ∃ A : Finset ℕ, A.card = m ∧ (∀ a ∈ A, 0 < a) ∧
      (∃ c : Fin r, ∀ x ∈ HindmanFSFP.FS A ∪ HindmanFSFP.FP A, χ x = c) ∧
      (HindmanFSFP.FS A).card = 2 ^ m - 1 ∧ (HindmanFSFP.FP A).card = 2 ^ m - 1 ∧
      HindmanFSFP.FS A ∩ HindmanFSFP.FP A = A := by sorry
