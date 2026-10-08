import Mathlib
set_option autoImplicit false

namespace GM

noncomputable section

/-- Index set E = {(ℓ,j,k) | 1 ≤ k ≤ c(j), 0 ≤ ℓ < j}, encoded as triples (ℓ,j,k). -/
def Idx (c : ℕ → ℕ) : Type := {t : ℕ × ℕ × ℕ // t.1 < t.2.1 ∧ 1 ≤ t.2.2 ∧ t.2.2 ≤ c t.2.1}

variable {c : ℕ → ℕ}

def Idx.l (e : Idx c) : ℕ := e.1.1
def Idx.j (e : Idx c) : ℕ := e.1.2.1
def Idx.k (e : Idx c) : ℕ := e.1.2.2

/-- The generating symbols 𝒳. -/
inductive Gen (c : ℕ → ℕ) : Type
  | H1 (s : ℂˣ) : Gen c
  | H2 (s : ℂˣ) : Gen c
  | Xm (u : ℂ) : Gen c
  | Ym (u : ℂ) : Gen c
  | X (e : Idx c) (u : ℂ) : Gen c
  | Y (e : Idx c) (u : ℂ) : Gen c

def fH1 (s : ℂˣ) : FreeGroup (Gen c) := FreeGroup.of (Gen.H1 s)
def fH2 (s : ℂˣ) : FreeGroup (Gen c) := FreeGroup.of (Gen.H2 s)
def fXm (u : ℂ) : FreeGroup (Gen c) := FreeGroup.of (Gen.Xm u)
def fYm (u : ℂ) : FreeGroup (Gen c) := FreeGroup.of (Gen.Ym u)
def fX (e : Idx c) (u : ℂ) : FreeGroup (Gen c) := FreeGroup.of (Gen.X e u)
def fY (e : Idx c) (u : ℂ) : FreeGroup (Gen c) := FreeGroup.of (Gen.Y e u)

/-- c_{ℓ j} = (-1)^{ℓ+1} C(j-1,ℓ) (ℓ+1)(j-ℓ). -/
def cc (e : Idx c) : ℂ :=
  ((-1 : ℂ) ^ (e.l + 1)) * (((e.j - 1).choose e.l : ℕ) : ℂ) * ((e.l : ℂ) + 1) * ((e.j - e.l : ℕ) : ℂ)

/-- (Re:0) -/
def wm (s : ℂˣ) : FreeGroup (Gen c) := fXm (s : ℂ) * fYm (-((s : ℂ)⁻¹)) * fXm (s : ℂ)

/-- (Im:0) -/
def wt (e : Idx c) (s : ℂˣ) : FreeGroup (Gen c) :=
  fX e (s : ℂ) * fY e (-((s : ℂ)⁻¹) / cc e) * fX e (s : ℂ)

/-- commutator (a,b) = a b a⁻¹ b⁻¹ -/
def comm (a b : FreeGroup (Gen c)) : FreeGroup (Gen c) := a * b * a⁻¹ * b⁻¹

/-- conjugation a b a⁻¹ -/
def conj (a b : FreeGroup (Gen c)) : FreeGroup (Gen c) := a * b * a⁻¹

/-- The set of defining relators; each constructor says `lhs = rhs` via the word `lhs * rhs⁻¹`. -/
inductive Rl (c : ℕ → ℕ) : FreeGroup (Gen c) → Prop
  | h1a (s t : ℂˣ) : Rl c (fH1 s * fH1 t * (fH1 (s * t))⁻¹)
  | h1b (s t : ℂˣ) : Rl c (fH2 s * fH2 t * (fH2 (s * t))⁻¹)
  | h2 (s t : ℂˣ) : Rl c (fH1 s * fH2 t * (fH2 t * fH1 s)⁻¹)
  | re1a (u v : ℂ) : Rl c (fXm u * fXm v * (fXm (u + v))⁻¹)
  | re1b (u v : ℂ) : Rl c (fYm u * fYm v * (fYm (u + v))⁻¹)
  | re2 (s t : ℂˣ) :
      Rl c (fYm (-(t : ℂ)) * fXm (s : ℂ) * fYm (t : ℂ) *
        (fXm (-((t : ℂ)⁻¹)) * fYm (-((t : ℂ) ^ 2 * (s : ℂ))) * fXm ((t : ℂ)⁻¹))⁻¹)
  | re3 (s : ℂˣ) : Rl c (wm s * wm 1 * (fH1 (-s) * fH2 (-s⁻¹))⁻¹)
  | re4a (u : ℂ) : Rl c (conj (wm 1) (fXm u) * (fYm (-u))⁻¹)
  | re4b (u : ℂ) : Rl c (conj (wm 1) (fYm u) * (fXm (-u))⁻¹)
  | re5a (s : ℂˣ) : Rl c (conj (wm 1) (fH1 s) * (fH2 s)⁻¹)
  | re5b (s : ℂˣ) : Rl c (conj (wm 1) (fH2 s) * (fH1 s)⁻¹)
  | re6a (s : ℂˣ) (u : ℂ) : Rl c (conj (fH1 s) (fXm u) * (fXm ((s : ℂ) * u))⁻¹)
  | re6b (s : ℂˣ) (u : ℂ) : Rl c (conj (fH2 s) (fXm u) * (fXm ((s : ℂ)⁻¹ * u))⁻¹)
  | re6c (s : ℂˣ) (u : ℂ) : Rl c (conj (fH1 s) (fYm u) * (fYm ((s : ℂ)⁻¹ * u))⁻¹)
  | re6d (s : ℂˣ) (u : ℂ) : Rl c (conj (fH2 s) (fYm u) * (fYm ((s : ℂ) * u))⁻¹)
  | im1a (e : Idx c) (u v : ℂ) : Rl c (fX e u * fX e v * (fX e (u + v))⁻¹)
  | im1b (e : Idx c) (u v : ℂ) : Rl c (fY e u * fY e v * (fY e (u + v))⁻¹)
  | im2 (e : Idx c) (s t : ℂˣ) :
      Rl c (fY e (-(t : ℂ)) * fX e (s : ℂ) * fY e (t : ℂ) *
        (fX e (-((t : ℂ)⁻¹) / cc e) * fY e (-(cc e) * (t : ℂ) ^ 2 * (s : ℂ)) *
          fX e ((t : ℂ)⁻¹ / cc e))⁻¹)
  | im3 (e : Idx c) (s : ℂˣ) :
      Rl c (wt e (s ^ ((e.l + 1) * (e.j - e.l))) * wt e 1 *
        (fH1 ((-s) ^ (e.j - e.l)) * fH2 ((-s) ^ (e.l + 1)))⁻¹)
  | im4a (e : Idx c) (u : ℂ) : Rl c (conj (wt e 1) (fX e u) * (fY e (-u / cc e))⁻¹)
  | im4b (e : Idx c) (u : ℂ) : Rl c (conj (wt e 1) (fY e u) * (fX e (-(cc e) * u))⁻¹)
  | im5a (e : Idx c) (s : ℂˣ) :
      Rl c (conj (wt e 1) (fH1 (s ^ (e.j - e.l))) * (fH2 (s ^ (-((e.l : ℤ) + 1))))⁻¹)
  | im5b (e : Idx c) (s : ℂˣ) :
      Rl c (conj (wt e 1) (fH2 (s ^ (e.l + 1))) * (fH1 (s ^ (-((e.j : ℤ) - e.l))))⁻¹)
  | im6a (e : Idx c) (s : ℂˣ) (u : ℂ) :
      Rl c (conj (fH1 s) (fX e u) * (fX e ((s : ℂ) ^ (e.l + 1) * u))⁻¹)
  | im6b (e : Idx c) (s : ℂˣ) (u : ℂ) :
      Rl c (conj (fH2 s) (fX e u) * (fX e ((s : ℂ) ^ (e.j - e.l) * u))⁻¹)
  | im6c (e : Idx c) (s : ℂˣ) (u : ℂ) :
      Rl c (conj (fH1 s) (fY e u) * (fY e ((s : ℂ) ^ (-((e.l : ℤ) + 1)) * u))⁻¹)
  | im6d (e : Idx c) (s : ℂˣ) (u : ℂ) :
      Rl c (conj (fH2 s) (fY e u) * (fY e ((s : ℂ) ^ (-((e.j : ℤ) - e.l)) * u))⁻¹)
  | u1a (e : Idx c) (h : e.l + 1 = e.j) (u v : ℂ) : Rl c (comm (fXm u) (fX e v))
  | u1b (e : Idx c) (h : e.l + 1 = e.j) (u v : ℂ) : Rl c (comm (fYm u) (fY e v))
  | u1c (e : Idx c) (h : e.l = 0) (u v : ℂ) : Rl c (comm (fYm u) (fX e v))
  | u1d (e : Idx c) (h : e.l = 0) (u v : ℂ) : Rl c (comm (fXm u) (fY e v))
  | u2 (e f : Idx c) (h : e.j ≠ f.j ∨ e.k ≠ f.k ∨ 1 < (((e.l : ℤ) - f.l)).natAbs) (u v : ℂ) :
      Rl c (comm (fX e u) (fY f v))
  | u3a (e f : Idx c) (k : ℕ) (he : e.1 = (1, 2, k)) (hf : f.1 = (0, 2, k)) (u v : ℂ) :
      Rl c (comm (fX e u) (fY f v) * (fXm (u * v))⁻¹)
  | u3b (e f : Idx c) (k : ℕ) (he : e.1 = (0, 2, k)) (hf : f.1 = (1, 2, k)) (u v : ℂ) :
      Rl c (comm (fX e u) (fY f v) * (fYm (u * v))⁻¹)
  | u4a (e e' : Idx c) (h : e'.1 = (e.j - 1 - e.l, e.j, e.k)) (u : ℂ) :
      Rl c (conj (wm 1) (fX e u) * (fX e' ((-1 : ℂ) ^ (e.j - e.l - 1) * u))⁻¹)
  | u4b (e e' : Idx c) (h : e'.1 = (e.j - 1 - e.l, e.j, e.k)) (u : ℂ) :
      Rl c (conj (wm 1) (fY e u) * (fY e' ((-1 : ℂ) ^ (e.j - e.l - 1) * u))⁻¹)

/-- The group G(𝔪) = ⟨𝒳 | ℛ⟩. -/
abbrev G (c : ℕ → ℕ) : Type := PresentedGroup {r : FreeGroup (Gen c) | Rl c r}

end

end GM



theorem mk_rel_eq_one_in_G (c : ℕ → ℕ) (r : FreeGroup (GM.Gen c)) (h : GM.Rl c r) :
    (PresentedGroup.mk {r' : FreeGroup (GM.Gen c) | GM.Rl c r'} r : GM.G c) = 1 := by
  exact (PresentedGroup.one_of_mem h)

theorem Xm_add_in_G (c : ℕ → ℕ) (u v : ℂ) :
    (PresentedGroup.of (GM.Gen.Xm u : GM.Gen c) : GM.G c) * (PresentedGroup.of (GM.Gen.Xm v : GM.Gen c) : GM.G c)
      = (PresentedGroup.of (GM.Gen.Xm (u + v) : GM.Gen c) : GM.G c) := by
  have h := PresentedGroup.mk_eq_mk_of_mul_inv_mem (GM.Rl.re1a (c := c) u v)
  simpa [GM.fXm, GM.G, PresentedGroup.of] using h

theorem Xm_zero_in_G (c : ℕ → ℕ) :
    (PresentedGroup.of (GM.Gen.Xm (0 : ℂ) : GM.Gen c) : GM.G c) = 1 := by
  have h := PresentedGroup.one_of_mem (GM.Rl.re1a (c := c) (0 : ℂ) (0 : ℂ))
  simpa [add_zero] using h

theorem solution (c : ℕ → ℕ) (u : ℂ) :
    ((PresentedGroup.of (GM.Gen.Xm (c := c) u) : GM.G c))⁻¹ =
      (PresentedGroup.of (GM.Gen.Xm (c := c) (-u)) : GM.G c) := by
  apply inv_eq_of_mul_eq_one_right
  have h := Xm_add_in_G c u (-u)
  rw [add_neg_cancel, Xm_zero_in_G] at h
  exact h
