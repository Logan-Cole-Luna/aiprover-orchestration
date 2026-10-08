import Definitions.Def_gsimple_torus_unipotent_commute



theorem gsimple_torus_unipotent_commute (c : ℕ → ℕ) :
    ∀ h ∈ GM.torusSub c, ∀ u ∈ GM.unipSub c,
      ∃ u' ∈ GM.unipSub c, u * h = h * u' := by sorry
