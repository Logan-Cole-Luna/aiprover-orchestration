import Definitions.Def_gsimple_x_inverse



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
