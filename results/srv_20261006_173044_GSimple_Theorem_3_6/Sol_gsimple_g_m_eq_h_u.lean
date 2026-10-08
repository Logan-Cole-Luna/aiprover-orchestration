import Definitions.Def_gsimple_g_m_eq_h_u



theorem hu_mul (c : ℕ → ℕ) (a b : GM.Grp c)
    (ha : ∃ h ∈ GM.torusSub c, ∃ u ∈ GM.unipSub c, a = h * u)
    (hb : ∃ h ∈ GM.torusSub c, ∃ u ∈ GM.unipSub c, b = h * u) :
    ∃ h ∈ GM.torusSub c, ∃ u ∈ GM.unipSub c, a * b = h * u := by
  rcases ha with ⟨h₁, hh₁, u₁, hu₁, ha_eq⟩
  rcases hb with ⟨h₂, hh₂, u₂, hu₂, hb_eq⟩
  rcases gsimple_torus_unipotent_commute c h₂ hh₂ u₁ hu₁ with ⟨u₁', hu₁', hu_comm⟩
  refine ⟨h₁ * h₂, Subgroup.mul_mem _ hh₁ hh₂, u₁' * u₂, Subgroup.mul_mem _ hu₁' hu₂, ?_⟩
  calc
    a * b = (h₁ * u₁) * (h₂ * u₂) := by rw [ha_eq, hb_eq]
    _ = h₁ * (u₁ * h₂) * u₂ := by group
    _ = h₁ * (h₂ * u₁') * u₂ := by rw [hu_comm]
    _ = (h₁ * h₂) * (u₁' * u₂) := by group

theorem hu_inv (c : ℕ → ℕ) (a : GM.Grp c)
    (ha : ∃ h ∈ GM.torusSub c, ∃ u ∈ GM.unipSub c, a = h * u) :
    ∃ h ∈ GM.torusSub c, ∃ u ∈ GM.unipSub c, a⁻¹ = h * u := by
  rcases ha with ⟨h, hh, u, hu, rfl⟩
  have h_inv : h⁻¹ ∈ GM.torusSub c := Subgroup.inv_mem _ hh
  have u_inv : u⁻¹ ∈ GM.unipSub c := Subgroup.inv_mem _ hu
  have h_comm := gsimple_torus_unipotent_commute c (h⁻¹) h_inv (u⁻¹) u_inv
  rcases h_comm with ⟨u', hu', h_eq⟩
  refine ⟨h⁻¹, h_inv, u', hu', ?_⟩
  calc
    (h * u)⁻¹ = u⁻¹ * h⁻¹ := by group
    _ = h⁻¹ * u' := h_eq

theorem hu_one (c : ℕ → ℕ) :
    ∃ h ∈ GM.torusSub c, ∃ u ∈ GM.unipSub c, (1 : GM.Grp c) = h * u := by
  have h1 : (1 : GM.Grp c) ∈ GM.torusSub c := Subgroup.one_mem _
  have h2 : (1 : GM.Grp c) ∈ GM.unipSub c := Subgroup.one_mem _
  refine ⟨1, h1, 1, h2, ?_⟩
  simp

theorem hu_of_torus (c : ℕ → ℕ) (x : GM.Grp c) (hx : x ∈ GM.torusSub c) :
    ∃ h ∈ GM.torusSub c, ∃ u ∈ GM.unipSub c, x = h * u := by
  refine ⟨x, hx, 1, Subgroup.one_mem _, ?_⟩
  simp

theorem hu_of_unip (c : ℕ → ℕ) (x : GM.Grp c) (hx : x ∈ GM.unipSub c) :
    ∃ h ∈ GM.torusSub c, ∃ u ∈ GM.unipSub c, x = h * u := by
  refine ⟨1, Subgroup.one_mem _, x, hx, ?_⟩
  simp

theorem hu_gen (c : ℕ → ℕ) (g : GM.Gen c) :
    ∃ h ∈ GM.torusSub c, ∃ u ∈ GM.unipSub c,
      (PresentedGroup.of g : GM.Grp c) = h * u := by
  induction g with
  | H1 s =>
      refine ⟨PresentedGroup.of (GM.Gen.H1 s), Subgroup.subset_closure (by
        refine ⟨s, Or.inl rfl⟩), 1, Subgroup.one_mem _, ?_⟩
      rfl
  | H2 s =>
      refine ⟨PresentedGroup.of (GM.Gen.H2 s), Subgroup.subset_closure (by
        refine ⟨s, Or.inr rfl⟩), 1, Subgroup.one_mem _, ?_⟩
      rfl
  | Xm u =>
      refine ⟨1, Subgroup.one_mem _, PresentedGroup.of (GM.Gen.Xm u),
        Subgroup.subset_closure (Or.inl ⟨u, rfl⟩), ?_⟩
      rfl
  | Ym u =>
      refine ⟨1, Subgroup.one_mem _, PresentedGroup.of (GM.Gen.Ym u),
        Subgroup.subset_closure (Or.inr (Or.inl ⟨u, rfl⟩)), ?_⟩
      rfl
  | X l j k h u =>
      refine ⟨1, Subgroup.one_mem _, PresentedGroup.of (GM.Gen.X l j k h u),
        Subgroup.subset_closure (Or.inr (Or.inr (Or.inl ⟨l, j, k, h, u, rfl⟩))), ?_⟩
      rfl
  | Y l j k h u =>
      refine ⟨1, Subgroup.one_mem _, PresentedGroup.of (GM.Gen.Y l j k h u),
        Subgroup.subset_closure (Or.inr (Or.inr (Or.inr ⟨l, j, k, h, u, rfl⟩))), ?_⟩
      rfl

theorem solution (c : ℕ → ℕ) :
    ∀ g : GM.Grp c, ∃ h ∈ GM.torusSub c, ∃ u ∈ GM.unipSub c, g = h * u := by
  intro g
  let S : Set (GM.Grp c) := {x | ∃ h ∈ GM.torusSub c, ∃ u ∈ GM.unipSub c, x = h * u}
  let N : Subgroup (GM.Grp c) :=
    { carrier := S
      mul_mem' := fun {a b} ha hb => hu_mul c a b ha hb
      one_mem' := hu_one c
      inv_mem' := fun {a} ha => hu_inv c a ha }
  have hN : N = ⊤ := subgroup_top_of_gens c N (fun x => hu_gen c x)
  have hg : g ∈ N := by
    rw [hN]
    exact Subgroup.mem_top g
  exact hg
