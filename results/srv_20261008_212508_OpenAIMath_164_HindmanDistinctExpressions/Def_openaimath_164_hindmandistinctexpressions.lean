import Mathlib

namespace HindmanFSFP

/-- FS(A): the set of all sums over nonempty subsets of A. -/
def FS (A : Finset ℕ) : Finset ℕ :=
  (A.powerset.filter (fun B => B.Nonempty)).image (fun B => ∑ a ∈ B, a)

/-- FP(A): the set of all products over nonempty subsets of A. -/
def FP (A : Finset ℕ) : Finset ℕ :=
  (A.powerset.filter (fun B => B.Nonempty)).image (fun B => ∏ a ∈ B, a)

/-- Theorem 1.1 (Hindman's FS+FP conjecture with growth conditions), stated as a
proposition so that it can be used as a hypothesis. Indices run over `Fin m`, so
`a ⟨0, _⟩` plays the role of `a_1`. -/
def Thm11 : Prop :=
  ∀ (r m : ℕ), 1 ≤ r → 1 ≤ m → ∀ (R D : ℝ), 2 ≤ R → 1 ≤ D →
    ∀ χ : ℕ → Fin r, ∃ (a : Fin m → ℕ) (c : Fin r),
      StrictMono a ∧ (∀ j, 0 < a j) ∧
      (∀ J : Finset (Fin m), J.Nonempty →
        χ (∑ j ∈ J, a j) = c ∧ χ (∏ j ∈ J, a j) = c) ∧
      (∀ d : Fin m, d.val = 0 → R < (a d : ℝ)) ∧
      (∀ d : Fin m, 1 ≤ d.val →
        R * ((((∑ k ∈ Finset.Iio d, a k : ℕ) : ℝ)
              + ((∏ k ∈ Finset.Iio d, a k : ℕ) : ℝ)) ^ D) < (a d : ℝ))

end HindmanFSFP

theorem setting_mem_FS (A : Finset ℕ) (x : ℕ) :
    x ∈ HindmanFSFP.FS A ↔ ∃ B : Finset ℕ, B ⊆ A ∧ B.Nonempty ∧ ∑ a ∈ B, a = x := by
  simp only [HindmanFSFP.FS, Finset.mem_image, Finset.mem_filter, Finset.mem_powerset, and_assoc]

theorem setting_mem_FP (A : Finset ℕ) (x : ℕ) :
    x ∈ HindmanFSFP.FP A ↔ ∃ B : Finset ℕ, B ⊆ A ∧ B.Nonempty ∧ ∏ a ∈ B, a = x := by
  simp only [HindmanFSFP.FP, Finset.mem_image, Finset.mem_filter, Finset.mem_powerset, and_assoc]
