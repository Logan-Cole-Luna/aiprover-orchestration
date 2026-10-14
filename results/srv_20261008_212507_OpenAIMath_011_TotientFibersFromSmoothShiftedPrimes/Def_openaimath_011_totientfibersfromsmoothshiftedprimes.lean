import Mathlib

/-- `g(n) = #{m ≥ 1 : φ(m) = n}`. The fiber is finite (Lemma 1). If it were infinite,
`Nat.card` would return `0`, which could only make the theorem harder. -/
noncomputable def totientFiberCard (n : ℕ) : ℕ :=
  Nat.card {m : ℕ // 0 < m ∧ Nat.totient m = n}

/-- `m` is `y`-smooth: every prime factor of `m` is at most `y`.
(For `m > 1` this is `P⁺(m) ≤ y`.) -/
def IsSmoothNat (m : ℕ) (y : ℝ) : Prop :=
  ∀ q ∈ m.primeFactors, (q : ℝ) ≤ y

/-- Hypothesis (H): for every `0 < δ < 1/4` and every `η > 0` there is `x₀` such that
for all real `x ≥ x₀`, `#{p prime : 2x < p ≤ 5x, P⁺(p-1) ≤ x^δ} ≥ x^(1-η)`. -/
def SmoothShiftedPrimesHypothesis : Prop :=
  ∀ δ : ℝ, 0 < δ → δ < 1 / 4 → ∀ η : ℝ, 0 < η →
    ∃ x₀ : ℝ, ∀ x : ℝ, x₀ ≤ x →
      (Nat.card {p : ℕ // p.Prime ∧ 2 * x < (p : ℝ) ∧ (p : ℝ) ≤ 5 * x ∧
          IsSmoothNat (p - 1) (x ^ δ)} : ℝ) ≥ x ^ (1 - η)
