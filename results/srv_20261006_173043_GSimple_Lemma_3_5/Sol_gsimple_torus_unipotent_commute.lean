import Definitions.Def_gsimple_torus_unipotent_commute



theorem conj_inv_rewrite {G : Type*} [Group G] (h u : G) :
    u * h = h * (h⁻¹ * u * h⁻¹⁻¹) := by
  group

theorem solution (c : ℕ → ℕ) :
    ∀ h ∈ GM.torusSub c, ∀ u ∈ GM.unipSub c,
      ∃ u' ∈ GM.unipSub c, u * h = h * u' := by
  intro h hh u hu
  refine ⟨h⁻¹ * u * h⁻¹⁻¹,
    gsimple_torus_normalizes_unipotent c h⁻¹ (Subgroup.inv_mem _ hh) u hu, ?_⟩
  exact conj_inv_rewrite h u
