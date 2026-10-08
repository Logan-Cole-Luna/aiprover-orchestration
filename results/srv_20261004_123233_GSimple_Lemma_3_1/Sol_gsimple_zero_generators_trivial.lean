import Definitions.Def_gsimple_zero_generators_trivial



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
