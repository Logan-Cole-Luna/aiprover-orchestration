import Mathlib

namespace GM

/-- Index set E: (ℓ,j,k) with 0 ≤ ℓ < j and 1 ≤ k ≤ c(j). -/
def Valid (c : ℕ → ℕ) (l j k : ℕ) : Prop := l < j ∧ 1 ≤ k ∧ k ≤ c j

/-- The generating symbols. -/
inductive Gen (c : ℕ → ℕ) : Type
  | H1 : ℂˣ → Gen c
  | H2 : ℂˣ → Gen c
  | Xm : ℂ → Gen c
  | Ym : ℂ → Gen c
  | X (l j k : ℕ) (h : Valid c l j k) (u : ℂ) : Gen c
  | Y (l j k : ℕ) (h : Valid c l j k) (u : ℂ) : Gen c

abbrev F (c : ℕ → ℕ) := FreeGroup (Gen c)

def h1 {c : ℕ → ℕ} (s : ℂˣ) : F c := FreeGroup.of (Gen.H1 s)
def h2 {c : ℕ → ℕ} (s : ℂˣ) : F c := FreeGroup.of (Gen.H2 s)
def xm {c : ℕ → ℕ} (u : ℂ) : F c := FreeGroup.of (Gen.Xm u)
def ym {c : ℕ → ℕ} (u : ℂ) : F c := FreeGroup.of (Gen.Ym u)
def xx {c : ℕ → ℕ} (l j k : ℕ) (h : Valid c l j k) (u : ℂ) : F c :=
  FreeGroup.of (Gen.X l j k h u)
def yy {c : ℕ → ℕ} (l j k : ℕ) (h : Valid c l j k) (u : ℂ) : F c :=
  FreeGroup.of (Gen.Y l j k h u)

/-- c_{ℓ j} = (-1)^(ℓ+1) * binom(j-1,ℓ) * (ℓ+1) * (j-ℓ). -/
noncomputable def cc (l j : ℕ) : ℂ :=
  (-1 : ℂ) ^ (l + 1) * (((j - 1).choose l : ℕ) : ℂ) * ((l : ℂ) + 1) * ((j - l : ℕ) : ℂ)

/-- commutator (a,b) = a b a⁻¹ b⁻¹ -/
def cm {c : ℕ → ℕ} (a b : F c) : F c := a * b * a⁻¹ * b⁻¹

/-- relator for the equation a = b -/
def eqr {c : ℕ → ℕ} (a b : F c) : F c := a * b⁻¹

noncomputable def wm {c : ℕ → ℕ} (s : ℂˣ) : F c :=
  xm (s : ℂ) * ym (-((s : ℂ)⁻¹)) * xm (s : ℂ)

noncomputable def ww {c : ℕ → ℕ} (l j k : ℕ) (h : Valid c l j k) (s : ℂˣ) : F c :=
  xx l j k h (s : ℂ) * yy l j k h (-((s : ℂ)⁻¹) / cc l j) * xx l j k h (s : ℂ)

/-- The defining relators. -/
inductive Rel (c : ℕ → ℕ) : F c → Prop
  | H1a (s t : ℂˣ) : Rel c (eqr (h1 s * h1 t) (h1 (s * t)))
  | H1b (s t : ℂˣ) : Rel c (eqr (h2 s * h2 t) (h2 (s * t)))
  | H2 (s t : ℂˣ) : Rel c (eqr (h1 s * h2 t) (h2 t * h1 s))
  | Re1a (u v : ℂ) : Rel c (eqr (xm u * xm v) (xm (u + v)))
  | Re1b (u v : ℂ) : Rel c (eqr (ym u * ym v) (ym (u + v)))
  | Re2 (s t : ℂˣ) : Rel c (eqr (ym (-(t : ℂ)) * xm (s : ℂ) * ym (t : ℂ))
      (xm (-((t : ℂ)⁻¹)) * ym (-((t : ℂ) ^ 2) * (s : ℂ)) * xm ((t : ℂ)⁻¹)))
  | Re3 (s : ℂˣ) : Rel c (eqr (wm s * wm 1) (h1 (-s) * h2 (-(s⁻¹))))
  | Re4a (u : ℂ) : Rel c (eqr (wm 1 * xm u * (wm 1)⁻¹) (ym (-u)))
  | Re4b (u : ℂ) : Rel c (eqr (wm 1 * ym u * (wm 1)⁻¹) (xm (-u)))
  | Re5a (s : ℂˣ) : Rel c (eqr (wm 1 * h1 s * (wm 1)⁻¹) (h2 s))
  | Re5b (s : ℂˣ) : Rel c (eqr (wm 1 * h2 s * (wm 1)⁻¹) (h1 s))
  | Re6a (s : ℂˣ) (u : ℂ) : Rel c (eqr (h1 s * xm u * (h1 s)⁻¹) (xm ((s : ℂ) * u)))
  | Re6b (s : ℂˣ) (u : ℂ) : Rel c (eqr (h2 s * xm u * (h2 s)⁻¹) (xm ((s : ℂ)⁻¹ * u)))
  | Re6c (s : ℂˣ) (u : ℂ) : Rel c (eqr (h1 s * ym u * (h1 s)⁻¹) (ym ((s : ℂ)⁻¹ * u)))
  | Re6d (s : ℂˣ) (u : ℂ) : Rel c (eqr (h2 s * ym u * (h2 s)⁻¹) (ym ((s : ℂ) * u)))
  | Im1a (l j k : ℕ) (h : Valid c l j k) (u v : ℂ) :
      Rel c (eqr (xx l j k h u * xx l j k h v) (xx l j k h (u + v)))
  | Im1b (l j k : ℕ) (h : Valid c l j k) (u v : ℂ) :
      Rel c (eqr (yy l j k h u * yy l j k h v) (yy l j k h (u + v)))
  | Im2 (l j k : ℕ) (h : Valid c l j k) (s t : ℂˣ) :
      Rel c (eqr (yy l j k h (-(t : ℂ)) * xx l j k h (s : ℂ) * yy l j k h (t : ℂ))
        (xx l j k h (-((t : ℂ)⁻¹) / cc l j) *
          yy l j k h (-(cc l j) * (t : ℂ) ^ 2 * (s : ℂ)) *
          xx l j k h ((t : ℂ)⁻¹ / cc l j)))
  | Im3 (l j k : ℕ) (h : Valid c l j k) (s : ℂˣ) :
      Rel c (eqr (ww l j k h (s ^ ((l + 1) * (j - l))) * ww l j k h 1)
        (h1 ((-s) ^ (j - l)) * h2 ((-s) ^ (l + 1))))
  | Im4a (l j k : ℕ) (h : Valid c l j k) (u : ℂ) :
      Rel c (eqr (ww l j k h 1 * xx l j k h u * (ww l j k h 1)⁻¹) (yy l j k h (-u / cc l j)))
  | Im4b (l j k : ℕ) (h : Valid c l j k) (u : ℂ) :
      Rel c (eqr (ww l j k h 1 * yy l j k h u * (ww l j k h 1)⁻¹) (xx l j k h (-(cc l j) * u)))
  | Im5a (l j k : ℕ) (h : Valid c l j k) (s : ℂˣ) :
      Rel c (eqr (ww l j k h 1 * h1 (s ^ (j - l)) * (ww l j k h 1)⁻¹) (h2 ((s ^ (l + 1))⁻¹)))
  | Im5b (l j k : ℕ) (h : Valid c l j k) (s : ℂˣ) :
      Rel c (eqr (ww l j k h 1 * h2 (s ^ (l + 1)) * (ww l j k h 1)⁻¹) (h1 ((s ^ (j - l))⁻¹)))
  | Im6a (l j k : ℕ) (h : Valid c l j k) (s : ℂˣ) (u : ℂ) :
      Rel c (eqr (h1 s * xx l j k h u * (h1 s)⁻¹) (xx l j k h ((s : ℂ) ^ (l + 1) * u)))
  | Im6b (l j k : ℕ) (h : Valid c l j k) (s : ℂˣ) (u : ℂ) :
      Rel c (eqr (h2 s * xx l j k h u * (h2 s)⁻¹) (xx l j k h ((s : ℂ) ^ (j - l) * u)))
  | Im6c (l j k : ℕ) (h : Valid c l j k) (s : ℂˣ) (u : ℂ) :
      Rel c (eqr (h1 s * yy l j k h u * (h1 s)⁻¹) (yy l j k h (((s : ℂ) ^ (l + 1))⁻¹ * u)))
  | Im6d (l j k : ℕ) (h : Valid c l j k) (s : ℂˣ) (u : ℂ) :
      Rel c (eqr (h2 s * yy l j k h u * (h2 s)⁻¹) (yy l j k h (((s : ℂ) ^ (j - l))⁻¹ * u)))
  | U1a (j k : ℕ) (h : Valid c (j - 1) j k) (u v : ℂ) :
      Rel c (cm (xm u) (xx (j - 1) j k h v))
  | U1b (j k : ℕ) (h : Valid c (j - 1) j k) (u v : ℂ) :
      Rel c (cm (ym u) (yy (j - 1) j k h v))
  | U1c (j k : ℕ) (h : Valid c 0 j k) (u v : ℂ) :
      Rel c (cm (ym u) (xx 0 j k h v))
  | U1d (j k : ℕ) (h : Valid c 0 j k) (u v : ℂ) :
      Rel c (cm (xm u) (yy 0 j k h v))
  | U2 (l j k : ℕ) (h : Valid c l j k) (m p q : ℕ) (h' : Valid c m p q) (u v : ℂ)
      (hne : j ≠ p ∨ k ≠ q ∨ 1 < Int.natAbs ((l : ℤ) - (m : ℤ))) :
      Rel c (cm (xx l j k h u) (yy m p q h' v))
  | U3a (k : ℕ) (h : Valid c 1 2 k) (h' : Valid c 0 2 k) (u v : ℂ) :
      Rel c (eqr (cm (xx 1 2 k h u) (yy 0 2 k h' v)) (xm (u * v)))
  | U3b (k : ℕ) (h : Valid c 0 2 k) (h' : Valid c 1 2 k) (u v : ℂ) :
      Rel c (eqr (cm (xx 0 2 k h u) (yy 1 2 k h' v)) (ym (u * v)))
  | U4a (l j k : ℕ) (h : Valid c l j k) (h' : Valid c (j - 1 - l) j k) (u : ℂ) :
      Rel c (eqr (wm 1 * xx l j k h u * (wm 1)⁻¹)
        (xx (j - 1 - l) j k h' ((-1 : ℂ) ^ (j - l - 1) * u)))
  | U4b (l j k : ℕ) (h : Valid c l j k) (h' : Valid c (j - 1 - l) j k) (u : ℂ) :
      Rel c (eqr (wm 1 * yy l j k h u * (wm 1)⁻¹)
        (yy (j - 1 - l) j k h' ((-1 : ℂ) ^ (j - l - 1) * u)))

def Rels (c : ℕ → ℕ) : Set (F c) := {r | Rel c r}

/-- The group G(m) = ⟨X | R⟩. -/
abbrev Grp (c : ℕ → ℕ) : Type := PresentedGroup (Rels c)

end GM
