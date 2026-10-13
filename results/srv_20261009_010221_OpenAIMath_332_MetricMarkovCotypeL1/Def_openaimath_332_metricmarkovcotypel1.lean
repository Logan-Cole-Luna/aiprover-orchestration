import Mathlib

namespace MarkovCotype

/-- The real Banach space ℓ₁ of absolutely summable real sequences
(indexed by ℕ; the shift from {1,2,...} to {0,1,...} is an isometry). -/
abbrev L1 : Type := lp (fun _ : ℕ => ℝ) 1

/-- A probability vector on [n]: nonnegative entries summing to one (zeros allowed). -/
def IsProbVec {n : ℕ} (π : Fin n → ℝ) : Prop :=
  (∀ i, 0 ≤ π i) ∧ ∑ i, π i = 1

/-- A stochastic matrix: nonnegative entries and every row sum equal to one. -/
def IsStochastic {n : ℕ} (A : Matrix (Fin n) (Fin n) ℝ) : Prop :=
  (∀ i j, 0 ≤ A i j) ∧ ∀ i, ∑ j, A i j = 1

/-- Reversibility (detailed balance) of `A` with respect to `π`. -/
def IsReversible {n : ℕ} (π : Fin n → ℝ) (A : Matrix (Fin n) (Fin n) ℝ) : Prop :=
  ∀ i j, π i * A i j = π j * A j i

end MarkovCotype
