import Definitions.Def_gsimple_x_inverse



theorem gsimple_x_inverse (c : ℕ → ℕ) (u : ℂ) :
    ((PresentedGroup.of (GM.Gen.Xm (c := c) u) : GM.G c))⁻¹ =
      (PresentedGroup.of (GM.Gen.Xm (c := c) (-u)) : GM.G c) := by sorry
