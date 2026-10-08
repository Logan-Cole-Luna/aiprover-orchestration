import Mathlib
set_option autoImplicit false

def IdxE (c : ℕ → ℕ) : Type := {x : ℕ × ℕ × ℕ // 1 ≤ x.2.2 ∧ x.2.2 ≤ c x.2.1 ∧ x.1 < x.2.1}

inductive Gen (c : ℕ → ℕ) : Type
  | H1 : ℂˣ → Gen c
  | H2 : ℂˣ → Gen c
  | Xm : ℂ → Gen c
  | Ym : ℂ → Gen c
  | X : IdxE c → ℂ → Gen c
  | Y : IdxE c → ℂ → Gen c

noncomputable def cc (l j : ℕ) : ℂ :=
  (-1 : ℂ) ^ (l + 1) * (Nat.choose (j - 1) l : ℂ) * ((l + 1 : ℕ) : ℂ) * ((j - l : ℕ) : ℂ)

def flipE {c : ℕ → ℕ} (e : IdxE c) : IdxE c :=
  ⟨(e.1.2.1 - 1 - e.1.1, e.1.2.1, e.1.2.2), ⟨e.2.1, e.2.2.1, by
    have h := e.2.2.2
    show e.1.2.1 - 1 - e.1.1 < e.1.2.1
    omega⟩⟩

section
variable {c : ℕ → ℕ}

def h1 (s : ℂˣ) : FreeGroup (Gen c) := FreeGroup.of (Gen.H1 s)
def h2 (s : ℂˣ) : FreeGroup (Gen c) := FreeGroup.of (Gen.H2 s)
def xm (u : ℂ) : FreeGroup (Gen c) := FreeGroup.of (Gen.Xm u)
def ym (u : ℂ) : FreeGroup (Gen c) := FreeGroup.of (Gen.Ym u)
def xx (e : IdxE c) (u : ℂ) : FreeGroup (Gen c) := FreeGroup.of (Gen.X e u)
def yy (e : IdxE c) (u : ℂ) : FreeGroup (Gen c) := FreeGroup.of (Gen.Y e u)

noncomputable def wm (s : ℂˣ) : FreeGroup (Gen c) := xm (c := c) s * ym (-((s : ℂ)⁻¹)) * xm (c := c) s
noncomputable def wm1 : FreeGroup (Gen c) := wm (c := c) 1

noncomputable def ww (e : IdxE c) (s : ℂˣ) : FreeGroup (Gen c) :=
  xx e s * yy e (-((s : ℂ)⁻¹) / cc e.1.1 e.1.2.1) * xx e s
noncomputable def ww1 (e : IdxE c) : FreeGroup (Gen c) := ww e 1

def comm' (a b : FreeGroup (Gen c)) : FreeGroup (Gen c) := a * b * a⁻¹ * b⁻¹
def eqr (a b : FreeGroup (Gen c)) : FreeGroup (Gen c) := a * b⁻¹
def conj' (a b : FreeGroup (Gen c)) : FreeGroup (Gen c) := a * b * a⁻¹
end

inductive GRel (c : ℕ → ℕ) : FreeGroup (Gen c) → Prop
  | H1a (s t : ℂˣ) : GRel c (eqr (h1 s * h1 t) (h1 (s * t)))
  | H1b (s t : ℂˣ) : GRel c (eqr (h2 s * h2 t) (h2 (s * t)))
  | H2 (s t : ℂˣ) : GRel c (eqr (h1 s * h2 t) (h2 t * h1 s))
  | Re1a (u v : ℂ) : GRel c (eqr (xm u * xm v) (xm (u + v)))
  | Re1b (u v : ℂ) : GRel c (eqr (ym u * ym v) (ym (u + v)))
  | Re2 (s t : ℂˣ) : GRel c (eqr (ym (-(t : ℂ)) * xm (s : ℂ) * ym (t : ℂ))
      (xm (-((t : ℂ)⁻¹)) * ym (-((t : ℂ) ^ 2 * s)) * xm ((t : ℂ)⁻¹)))
  | Re3 (s : ℂˣ) : GRel c (eqr (wm s * wm1) (h1 (-s) * h2 (-(s⁻¹))))
  | Re4a (u : ℂ) : GRel c (eqr (conj' wm1 (xm u)) (ym (-u)))
  | Re4b (u : ℂ) : GRel c (eqr (conj' wm1 (ym u)) (xm (-u)))
  | Re5a (s : ℂˣ) : GRel c (eqr (conj' wm1 (h1 s)) (h2 s))
  | Re5b (s : ℂˣ) : GRel c (eqr (conj' wm1 (h2 s)) (h1 s))
  | Re6a (s : ℂˣ) (u : ℂ) : GRel c (eqr (conj' (h1 s) (xm u)) (xm ((s : ℂ) * u)))
  | Re6b (s : ℂˣ) (u : ℂ) : GRel c (eqr (conj' (h2 s) (xm u)) (xm ((s : ℂ)⁻¹ * u)))
  | Re6c (s : ℂˣ) (u : ℂ) : GRel c (eqr (conj' (h1 s) (ym u)) (ym ((s : ℂ)⁻¹ * u)))
  | Re6d (s : ℂˣ) (u : ℂ) : GRel c (eqr (conj' (h2 s) (ym u)) (ym ((s : ℂ) * u)))
  | Im1a (e : IdxE c) (u v : ℂ) : GRel c (eqr (xx e u * xx e v) (xx e (u + v)))
  | Im1b (e : IdxE c) (u v : ℂ) : GRel c (eqr (yy e u * yy e v) (yy e (u + v)))
  | Im2 (e : IdxE c) (s t : ℂˣ) :
      GRel c (eqr (yy e (-(t : ℂ)) * xx e (s : ℂ) * yy e (t : ℂ))
        (xx e (-((t : ℂ)⁻¹) / cc e.1.1 e.1.2.1) *
          yy e (-(cc e.1.1 e.1.2.1 * (t : ℂ) ^ 2 * (s : ℂ))) *
          xx e ((t : ℂ)⁻¹ / cc e.1.1 e.1.2.1)))
  | Im3 (e : IdxE c) (s : ℂˣ) :
      GRel c (eqr (ww (c := c) e (s ^ ((e.1.1 + 1) * (e.1.2.1 - e.1.1))) * ww1 e)
        (h1 ((-s) ^ (e.1.2.1 - e.1.1)) * h2 ((-s) ^ (e.1.1 + 1))))
  | Im4a (e : IdxE c) (u : ℂ) :
      GRel c (eqr (conj' (ww1 e) (xx e u)) (yy e (-u / cc e.1.1 e.1.2.1)))
  | Im4b (e : IdxE c) (u : ℂ) :
      GRel c (eqr (conj' (ww1 e) (yy e u)) (xx e (-(cc e.1.1 e.1.2.1) * u)))
  | Im5a (e : IdxE c) (s : ℂˣ) :
      GRel c (eqr (conj' (ww1 e) (h1 (s ^ (e.1.2.1 - e.1.1)))) (h2 ((s ^ (e.1.1 + 1))⁻¹)))
  | Im5b (e : IdxE c) (s : ℂˣ) :
      GRel c (eqr (conj' (ww1 e) (h2 (s ^ (e.1.1 + 1)))) (h1 ((s ^ (e.1.2.1 - e.1.1))⁻¹)))
  | Im6a (e : IdxE c) (s : ℂˣ) (u : ℂ) :
      GRel c (eqr (conj' (h1 s) (xx e u)) (xx e ((s : ℂ) ^ (e.1.1 + 1) * u)))
  | Im6b (e : IdxE c) (s : ℂˣ) (u : ℂ) :
      GRel c (eqr (conj' (h2 s) (xx e u)) (xx e ((s : ℂ) ^ (e.1.2.1 - e.1.1) * u)))
  | Im6c (e : IdxE c) (s : ℂˣ) (u : ℂ) :
      GRel c (eqr (conj' (h1 s) (yy e u)) (yy e (((s : ℂ) ^ (e.1.1 + 1))⁻¹ * u)))
  | Im6d (e : IdxE c) (s : ℂˣ) (u : ℂ) :
      GRel c (eqr (conj' (h2 s) (yy e u)) (yy e (((s : ℂ) ^ (e.1.2.1 - e.1.1))⁻¹ * u)))
  | U1a (e : IdxE c) (u v : ℂ) (h : e.1.1 + 1 = e.1.2.1) :
      GRel c (comm' (xm u) (xx e v))
  | U1b (e : IdxE c) (u v : ℂ) (h : e.1.1 + 1 = e.1.2.1) :
      GRel c (comm' (ym u) (yy e v))
  | U1c (e : IdxE c) (u v : ℂ) (h : e.1.1 = 0) :
      GRel c (comm' (ym u) (xx e v))
  | U1d (e : IdxE c) (u v : ℂ) (h : e.1.1 = 0) :
      GRel c (comm' (xm u) (yy e v))
  | U2 (e f : IdxE c) (u v : ℂ)
      (h : e.1.2.1 ≠ f.1.2.1 ∨ e.1.2.2 ≠ f.1.2.2 ∨
        1 < |((e.1.1 : ℤ) - (f.1.1 : ℤ))|) :
      GRel c (comm' (xx e u) (yy f v))
  | U3a (e f : IdxE c) (u v : ℂ)
      (he : e.1.1 = 1 ∧ e.1.2.1 = 2) (hf : f.1.1 = 0 ∧ f.1.2.1 = 2)
      (hk : e.1.2.2 = f.1.2.2) :
      GRel c (eqr (comm' (xx e u) (yy f v)) (xm (u * v)))
  | U3b (e f : IdxE c) (u v : ℂ)
      (he : e.1.1 = 0 ∧ e.1.2.1 = 2) (hf : f.1.1 = 1 ∧ f.1.2.1 = 2)
      (hk : e.1.2.2 = f.1.2.2) :
      GRel c (eqr (comm' (xx e u) (yy f v)) (ym (u * v)))
  | U4a (e : IdxE c) (u : ℂ) :
      GRel c (eqr (conj' wm1 (xx e u))
        (xx (flipE e) ((-1 : ℂ) ^ (e.1.2.1 - e.1.1 - 1) * u)))
  | U4b (e : IdxE c) (u : ℂ) :
      GRel c (eqr (conj' wm1 (yy e u))
        (yy (flipE e) ((-1 : ℂ) ^ (e.1.2.1 - e.1.1 - 1) * u)))

abbrev Gm (c : ℕ → ℕ) : Type := PresentedGroup {r : FreeGroup (Gen c) | GRel c r}



theorem add_zero_group_lemma {G : Type*} [Group G] (f : ℂ → G)
    (h : ∀ u v : ℂ, f (u + v) = f u * f v) : f 0 = 1 := by
  have h0 : f 0 = f 0 * f 0 := by
    calc
      f 0 = f (0 + 0) := by rw [zero_add]
      _ = f 0 * f 0 := h 0 0
  calc
    f 0 = f 0 * 1 := by simp
    _ = f 0 * (f 0 * (f 0)⁻¹) := by simp
    _ = (f 0 * f 0) * (f 0)⁻¹ := by rw [mul_assoc]
    _ = f 0 * (f 0)⁻¹ := by rw [← h0]
    _ = 1 := by simp

theorem mk_xm_add (c : ℕ → ℕ) (u v : ℂ) :
    (PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xm (u + v) : FreeGroup (Gen c)) : Gm c) =
      PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xm u : FreeGroup (Gen c)) *
      PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xm v : FreeGroup (Gen c)) := by
  have hrel := GRel.Re1a (c := c) u v
  have h2 : PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xm u * xm v) *
      (PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xm (u + v)))⁻¹ = 1 :=
    PresentedGroup.one_of_mem hrel
  have h1 : PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xm u * xm v) =
      PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xm u) *
      PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xm v) := by
    simp
  rw [h1] at h2
  exact (eq_of_mul_inv_eq_one h2).symm

theorem mk_ym_add (c : ℕ → ℕ) (u v : ℂ) :
    (PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (ym (u + v) : FreeGroup (Gen c)) : Gm c) =
      PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (ym u : FreeGroup (Gen c)) *
      PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (ym v : FreeGroup (Gen c)) := by

  have hrel : GRel c (eqr (ym u * ym v) (ym (u + v))) := GRel.Re1b u v
  have h_eq : (PresentedGroup.mk {r | GRel c r} (ym u * ym v) : Gm c) =
             (PresentedGroup.mk {r | GRel c r} (ym (u + v)) : Gm c) := by
    have h_one : (PresentedGroup.mk {r | GRel c r} (eqr (ym u * ym v) (ym (u + v))) : Gm c) = 1 :=
      PresentedGroup.one_of_mem hrel
    have h_mul_one : (PresentedGroup.mk {r | GRel c r} (ym u * ym v) : Gm c) *
                   ((PresentedGroup.mk {r | GRel c r} (ym (u + v)) : Gm c))⁻¹ = 1 := by
      calc
        (PresentedGroup.mk {r | GRel c r} (ym u * ym v) : Gm c) *
          ((PresentedGroup.mk {r | GRel c r} (ym (u + v)) : Gm c))⁻¹ =
          (PresentedGroup.mk {r | GRel c r} ((ym u * ym v) * (ym (u + v))⁻¹) : Gm c) := by
          simp
        _ = (PresentedGroup.mk {r | GRel c r} (eqr (ym u * ym v) (ym (u + v))) : Gm c) := rfl
        _ = 1 := h_one
    calc
      (PresentedGroup.mk {r | GRel c r} (ym u * ym v) : Gm c) =
        ((PresentedGroup.mk {r | GRel c r} (ym u * ym v) : Gm c) * 1) := by simp
      _ = ((PresentedGroup.mk {r | GRel c r} (ym u * ym v) : Gm c) *
          (((PresentedGroup.mk {r | GRel c r} (ym (u + v)) : Gm c))⁻¹ *
           (PresentedGroup.mk {r | GRel c r} (ym (u + v)) : Gm c))) := by simp
      _ = ((PresentedGroup.mk {r | GRel c r} (ym u * ym v) : Gm c) *
           ((PresentedGroup.mk {r | GRel c r} (ym (u + v)) : Gm c))⁻¹) *
          (PresentedGroup.mk {r | GRel c r} (ym (u + v)) : Gm c) := by rw [mul_assoc]
      _ = 1 * (PresentedGroup.mk {r | GRel c r} (ym (u + v)) : Gm c) := by rw [h_mul_one]
      _ = (PresentedGroup.mk {r | GRel c r} (ym (u + v)) : Gm c) := by simp
  calc
    (PresentedGroup.mk {r | GRel c r} (ym (u + v)) : Gm c)
        = (PresentedGroup.mk {r | GRel c r} (ym u * ym v) : Gm c) := by rw [← h_eq]
    _ = (PresentedGroup.mk {r | GRel c r} (ym u) : Gm c) *
        (PresentedGroup.mk {r | GRel c r} (ym v) : Gm c) :=
      (PresentedGroup.mk {r | GRel c r}).map_mul (ym u) (ym v)

theorem mk_xx_add (c : ℕ → ℕ) (e : IdxE c) (u v : ℂ) :
    (PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xx e (u + v)) : Gm c) =
      PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xx e u) *
      PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xx e v) := by
  let rels : Set (FreeGroup (Gen c)) := {r | GRel c r}
  have h := GRel.Im1a e u v
  have hmem : (xx e u * xx e v) * (xx e (u + v))⁻¹ ∈ rels := h
  have h_eq : (PresentedGroup.mk rels) (xx e u * xx e v) = (PresentedGroup.mk rels) (xx e (u + v)) :=
    PresentedGroup.mk_eq_mk_of_mul_inv_mem hmem
  calc
    (PresentedGroup.mk rels) (xx e (u + v)) = (PresentedGroup.mk rels) (xx e u * xx e v) := by
      rw [h_eq]
    _ = (PresentedGroup.mk rels) (xx e u) * (PresentedGroup.mk rels) (xx e v) := by
      rw [(PresentedGroup.mk rels).map_mul]

theorem mk_yy_add (c : ℕ → ℕ) (e : IdxE c) (u v : ℂ) :
    (PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (yy e (u + v)) : Gm c) =
      PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (yy e u) *
      PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (yy e v) := by
  have h := GRel.Im1b e u v
  have h_eq : PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (yy e u * yy e v) =
              PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (yy e (u + v)) :=
    PresentedGroup.mk_eq_mk_of_mul_inv_mem h
  calc
    (PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (yy e (u + v)) : Gm c)
        = PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (yy e u * yy e v) := by
      rw [← h_eq]
    _ = PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (yy e u) *
        PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (yy e v) := by
      rw [map_mul]

theorem solution (c : ℕ → ℕ) :
    ((PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xm (c := c) 0) : Gm c) = 1) ∧
    ((PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (ym (c := c) 0) : Gm c) = 1) ∧
    (∀ e : IdxE c,
      (PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xx e 0) : Gm c) = 1) ∧
    (∀ e : IdxE c,
      (PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (yy e 0) : Gm c) = 1) := by
  refine ⟨?_, ?_, ?_, ?_⟩
  · exact add_zero_group_lemma
      (fun u : ℂ => (PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xm u : FreeGroup (Gen c)) : Gm c))
      (mk_xm_add c)
  · exact add_zero_group_lemma
      (fun u : ℂ => (PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (ym u : FreeGroup (Gen c)) : Gm c))
      (mk_ym_add c)
  · intro e
    exact add_zero_group_lemma
      (fun u : ℂ => (PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xx e u) : Gm c))
      (mk_xx_add c e)
  · intro e
    exact add_zero_group_lemma
      (fun u : ℂ => (PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (yy e u) : Gm c))
      (mk_yy_add c e)
