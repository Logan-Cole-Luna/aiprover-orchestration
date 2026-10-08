import Definitions.Def_gsimple_zero_generators_trivial



theorem gsimple_zero_generators_trivial (c : ℕ → ℕ) :
    ((PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xm (c := c) 0) : Gm c) = 1) ∧
    ((PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (ym (c := c) 0) : Gm c) = 1) ∧
    (∀ e : IdxE c,
      (PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (xx e 0) : Gm c) = 1) ∧
    (∀ e : IdxE c,
      (PresentedGroup.mk {r : FreeGroup (Gen c) | GRel c r} (yy e 0) : Gm c) = 1) := by sorry
