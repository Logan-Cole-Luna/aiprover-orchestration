import Definitions.Def_gsimple_torus_normalizes_unipotent



theorem h1_conj_unip (c : ℕ → ℕ) (s : ℂˣ) (g : GM.Grp c) (hg : g ∈ GM.unipGens c) :
    PresentedGroup.mk (GM.Rels c) (GM.h1 s) * g * (PresentedGroup.mk (GM.Rels c) (GM.h1 s))⁻¹
      ∈ GM.unipGens c := by


  rcases hg with (⟨u, hg_u⟩ | ⟨u, hg_u⟩ | ⟨l, j, k, h, u, hg_u⟩ | ⟨l, j, k, h, u, hg_u⟩)
  · 
    subst hg_u

    have hrel := GM.Rel.Re6a (c := c) s u
    have h_eq : PresentedGroup.mk (GM.Rels c) (GM.h1 s * GM.xm u * (GM.h1 s)⁻¹) =
        PresentedGroup.mk (GM.Rels c) (GM.xm ((s : ℂ) * u)) := by
      simpa [GM.eqr] using mk_eqr c (GM.h1 s * GM.xm u * (GM.h1 s)⁻¹) (GM.xm ((s : ℂ) * u)) hrel
    have h_target : PresentedGroup.mk (GM.Rels c) (GM.xm ((s : ℂ) * u)) ∈ GM.unipGens c := by
      refine Or.inl ⟨(s : ℂ) * u, ?_⟩
      rfl
    have h_eq' : PresentedGroup.mk (GM.Rels c) (GM.h1 s) * PresentedGroup.mk (GM.Rels c) (GM.xm u) *
      (PresentedGroup.mk (GM.Rels c) (GM.h1 s))⁻¹ = PresentedGroup.mk (GM.Rels c) (GM.xm ((s : ℂ) * u)) := by
      simpa [GM.xm, map_mul, map_inv] using h_eq
    rw [h_eq']
    exact h_target
  · 
    subst hg_u

    have hrel := GM.Rel.Re6c (c := c) s u
    have h_eq : PresentedGroup.mk (GM.Rels c) (GM.h1 s * GM.ym u * (GM.h1 s)⁻¹) =
        PresentedGroup.mk (GM.Rels c) (GM.ym (((s : ℂ)⁻¹) * u)) := by
      simpa [GM.eqr] using mk_eqr c (GM.h1 s * GM.ym u * (GM.h1 s)⁻¹) (GM.ym (((s : ℂ)⁻¹) * u)) hrel
    have h_target : PresentedGroup.mk (GM.Rels c) (GM.ym (((s : ℂ)⁻¹) * u)) ∈ GM.unipGens c := by
      refine Or.inr (Or.inl ⟨((s : ℂ)⁻¹) * u, ?_⟩)
      rfl
    have h_eq' : PresentedGroup.mk (GM.Rels c) (GM.h1 s) * PresentedGroup.mk (GM.Rels c) (GM.ym u) *
      (PresentedGroup.mk (GM.Rels c) (GM.h1 s))⁻¹ = PresentedGroup.mk (GM.Rels c) (GM.ym (((s : ℂ)⁻¹) * u)) := by
      simpa [GM.ym, map_mul, map_inv] using h_eq
    rw [h_eq']
    exact h_target
  · 
    subst hg_u

    have hrel := GM.Rel.Im6a (c := c) l j k h s u
    have h_eq : PresentedGroup.mk (GM.Rels c) (GM.h1 s * GM.xx l j k h u * (GM.h1 s)⁻¹) =
        PresentedGroup.mk (GM.Rels c) (GM.xx l j k h ((s : ℂ) ^ (l + 1) * u)) := by
      simpa [GM.eqr] using mk_eqr c (GM.h1 s * GM.xx l j k h u * (GM.h1 s)⁻¹) (GM.xx l j k h ((s : ℂ) ^ (l + 1) * u)) hrel
    have h_target : PresentedGroup.mk (GM.Rels c) (GM.xx l j k h ((s : ℂ) ^ (l + 1) * u)) ∈ GM.unipGens c := by
      refine Or.inr (Or.inr (Or.inl ⟨l, j, k, h, ((s : ℂ) ^ (l + 1) * u), ?_⟩))
      rfl
    have h_eq' : PresentedGroup.mk (GM.Rels c) (GM.h1 s) * PresentedGroup.mk (GM.Rels c) (GM.xx l j k h u) *
      (PresentedGroup.mk (GM.Rels c) (GM.h1 s))⁻¹ = PresentedGroup.mk (GM.Rels c) (GM.xx l j k h ((s : ℂ) ^ (l + 1) * u)) := by
      simpa [GM.xx, map_mul, map_inv] using h_eq
    rw [h_eq']
    exact h_target
  · 
    subst hg_u

    have hrel := GM.Rel.Im6c (c := c) l j k h s u
    have h_eq : PresentedGroup.mk (GM.Rels c) (GM.h1 s * GM.yy l j k h u * (GM.h1 s)⁻¹) =
        PresentedGroup.mk (GM.Rels c) (GM.yy l j k h (((s : ℂ) ^ (l + 1))⁻¹ * u)) := by
      simpa [GM.eqr] using mk_eqr c (GM.h1 s * GM.yy l j k h u * (GM.h1 s)⁻¹) (GM.yy l j k h (((s : ℂ) ^ (l + 1))⁻¹ * u)) hrel
    have h_target : PresentedGroup.mk (GM.Rels c) (GM.yy l j k h (((s : ℂ) ^ (l + 1))⁻¹ * u)) ∈ GM.unipGens c := by
      refine Or.inr (Or.inr (Or.inr ⟨l, j, k, h, (((s : ℂ) ^ (l + 1))⁻¹ * u), ?_⟩))
      rfl
    have h_eq' : PresentedGroup.mk (GM.Rels c) (GM.h1 s) * PresentedGroup.mk (GM.Rels c) (GM.yy l j k h u) *
      (PresentedGroup.mk (GM.Rels c) (GM.h1 s))⁻¹ = PresentedGroup.mk (GM.Rels c) (GM.yy l j k h (((s : ℂ) ^ (l + 1))⁻¹ * u)) := by
      simpa [GM.yy, map_mul, map_inv] using h_eq
    rw [h_eq']
    exact h_target

theorem h2_conj_unip (c : ℕ → ℕ) (s : ℂˣ) (g : GM.Grp c) (hg : g ∈ GM.unipGens c) :
    PresentedGroup.mk (GM.Rels c) (GM.h2 s) * g * (PresentedGroup.mk (GM.Rels c) (GM.h2 s))⁻¹
      ∈ GM.unipGens c := by
  rcases hg with (⟨u, hg'⟩ | ⟨u, hg'⟩ | ⟨l, j, k, h, u, hg'⟩ | ⟨l, j, k, h, u, hg'⟩)
  · 
    subst hg'
    have h_rel := GM.Rel.Re6b (c := c) s u
    have h_eq := mk_eqr c (GM.h2 s * GM.xm u * (GM.h2 s)⁻¹) (GM.xm ((s : ℂ)⁻¹ * u)) h_rel
    have h_eq' : PresentedGroup.mk (GM.Rels c) (GM.h2 s) * PresentedGroup.mk (GM.Rels c) (GM.xm u) *
      (PresentedGroup.mk (GM.Rels c) (GM.h2 s))⁻¹ = PresentedGroup.mk (GM.Rels c) (GM.xm ((s : ℂ)⁻¹ * u)) := by
      simpa [PresentedGroup.mk, map_mul, map_inv] using h_eq
    rw [h_eq']
    exact Set.mem_setOf.mpr (Or.inl ⟨(s : ℂ)⁻¹ * u, rfl⟩)
  · 
    subst hg'
    have h_rel := GM.Rel.Re6d (c := c) s u
    have h_eq := mk_eqr c (GM.h2 s * GM.ym u * (GM.h2 s)⁻¹) (GM.ym ((s : ℂ) * u)) h_rel
    have h_eq' : PresentedGroup.mk (GM.Rels c) (GM.h2 s) * PresentedGroup.mk (GM.Rels c) (GM.ym u) *
      (PresentedGroup.mk (GM.Rels c) (GM.h2 s))⁻¹ = PresentedGroup.mk (GM.Rels c) (GM.ym ((s : ℂ) * u)) := by
      simpa [PresentedGroup.mk, map_mul, map_inv] using h_eq
    rw [h_eq']
    exact Set.mem_setOf.mpr (Or.inr (Or.inl ⟨(s : ℂ) * u, rfl⟩))
  · 
    subst hg'
    have h_rel := GM.Rel.Im6b (c := c) l j k h s u
    have h_eq := mk_eqr c (GM.h2 s * GM.xx l j k h u * (GM.h2 s)⁻¹) (GM.xx l j k h (((s : ℂ) ^ (j - l)) * u)) h_rel
    have h_eq' : PresentedGroup.mk (GM.Rels c) (GM.h2 s) * PresentedGroup.mk (GM.Rels c) (GM.xx l j k h u) *
      (PresentedGroup.mk (GM.Rels c) (GM.h2 s))⁻¹ = PresentedGroup.mk (GM.Rels c) (GM.xx l j k h (((s : ℂ) ^ (j - l)) * u)) := by
      simpa [PresentedGroup.mk, map_mul, map_inv] using h_eq
    rw [h_eq']
    exact Set.mem_setOf.mpr (Or.inr (Or.inr (Or.inl ⟨l, j, k, h, ((s : ℂ) ^ (j - l)) * u, rfl⟩)))
  · 
    subst hg'
    have h_rel := GM.Rel.Im6d (c := c) l j k h s u
    have h_eq := mk_eqr c (GM.h2 s * GM.yy l j k h u * (GM.h2 s)⁻¹) (GM.yy l j k h ((((s : ℂ) ^ (j - l))⁻¹) * u)) h_rel
    have h_eq' : PresentedGroup.mk (GM.Rels c) (GM.h2 s) * PresentedGroup.mk (GM.Rels c) (GM.yy l j k h u) *
      (PresentedGroup.mk (GM.Rels c) (GM.h2 s))⁻¹ = PresentedGroup.mk (GM.Rels c) (GM.yy l j k h ((((s : ℂ) ^ (j - l))⁻¹) * u)) := by
      simpa [PresentedGroup.mk, map_mul, map_inv] using h_eq
    rw [h_eq']
    exact Set.mem_setOf.mpr (Or.inr (Or.inr (Or.inr ⟨l, j, k, h, (((s : ℂ) ^ (j - l))⁻¹) * u, rfl⟩)))

theorem h1_inv_mk (c : ℕ → ℕ) (s : ℂˣ) :
    PresentedGroup.mk (GM.Rels c) (GM.h1 s⁻¹) = (PresentedGroup.mk (GM.Rels c) (GM.h1 s))⁻¹ := by
  let φ := PresentedGroup.mk (GM.Rels c)
  have hφ_mul : ∀ x y : GM.F c, φ (x * y) = φ x * φ y := by
    intro x y; exact MonoidHom.map_mul φ x y
  have hφ_inv : ∀ x : GM.F c, φ (x⁻¹) = (φ x)⁻¹ := by
    intro x; exact MonoidHom.map_inv φ x
  have h1_one : φ (GM.h1 1) = 1 := by
    have hRel : GM.Rel c (GM.eqr (GM.h1 1 * GM.h1 1) (GM.h1 (1 * 1))) :=
      GM.Rel.H1a (c := c) 1 1
    have h_one := PresentedGroup.one_of_mem (hx := hRel) (rels := GM.Rels c)
    have hcalc : φ (GM.eqr (GM.h1 1 * GM.h1 1) (GM.h1 (1 * 1))) =
      φ (GM.h1 1 * GM.h1 1) * (φ (GM.h1 (1 * 1)))⁻¹ := by
      simp [GM.eqr, hφ_mul, hφ_inv]
    rw [hcalc] at h_one
    rw [mul_one] at h_one
    rw [hφ_mul] at h_one
    calc
      φ (GM.h1 1) = (φ (GM.h1 1) * φ (GM.h1 1)) * (φ (GM.h1 1))⁻¹ := by group
      _ = 1 := h_one
  have hRel : GM.Rel c (GM.eqr (GM.h1 s * GM.h1 s⁻¹) (GM.h1 (s * s⁻¹))) :=
    GM.Rel.H1a (c := c) s s⁻¹
  have hRel' : GM.Rel c (GM.eqr (GM.h1 s * GM.h1 s⁻¹) (GM.h1 1)) := by
    simpa using hRel
  have h_eq := mk_eqr c (GM.h1 s * GM.h1 s⁻¹) (GM.h1 1) hRel'
  calc
    φ (GM.h1 s⁻¹) = 1 * φ (GM.h1 s⁻¹) := by simp
    _ = ((φ (GM.h1 s))⁻¹ * φ (GM.h1 s)) * φ (GM.h1 s⁻¹) := by simp
    _ = (φ (GM.h1 s))⁻¹ * (φ (GM.h1 s) * φ (GM.h1 s⁻¹)) := by group
    _ = (φ (GM.h1 s))⁻¹ * φ (GM.h1 s * GM.h1 s⁻¹) := by rw [hφ_mul]
    _ = (φ (GM.h1 s))⁻¹ * φ (GM.h1 1) := by rw [h_eq]
    _ = (φ (GM.h1 s))⁻¹ * 1 := by rw [h1_one]
    _ = (φ (GM.h1 s))⁻¹ := by simp

theorem h2_inv_mk (c : ℕ → ℕ) (s : ℂˣ) :
    PresentedGroup.mk (GM.Rels c) (GM.h2 s⁻¹) = (PresentedGroup.mk (GM.Rels c) (GM.h2 s))⁻¹ := by
  rw [h2_eq_conj c s⁻¹, h2_eq_conj c s]
  simp
  rw [h1_inv_mk c s]
  group

theorem torusGens_conj (c : ℕ → ℕ) :
    ∀ t ∈ GM.torusGens c, ∀ g ∈ GM.unipGens c, t * g * t⁻¹ ∈ GM.unipGens c := by
  intro t ht g hg
  rcases ht with ⟨s, hs⟩
  rcases hs with (hs | hs)
  · 
    rw [hs]
    exact h1_conj_unip c s g hg
  · 
    rw [hs]
    exact h2_conj_unip c s g hg

theorem torusGens_inv (c : ℕ → ℕ) :
    ∀ t ∈ GM.torusGens c, t⁻¹ ∈ GM.torusGens c := by
  intro t ht
  rcases ht with ⟨s, hs | hs⟩
  · rw [hs]
    refine ⟨s⁻¹, Or.inl ?_⟩
    rw [h1_inv_mk c s]
  · rw [hs]
    refine ⟨s⁻¹, Or.inr ?_⟩
    rw [h2_inv_mk c s]

theorem conj_closure_of_gens {G : Type*} [Group G] (T : Set G) (x : G)
    (hx : ∀ g ∈ T, x * g * x⁻¹ ∈ Subgroup.closure T) :
    ∀ u ∈ Subgroup.closure T, x * u * x⁻¹ ∈ Subgroup.closure T := by
  intro u hu
  induction hu using Subgroup.closure_induction with
  | mem g hg =>
      exact hx g hg
  | one =>
      simp
  | mul a b ha hb iha ihb =>
      have h : x * (a * b) * x⁻¹ = (x * a * x⁻¹) * (x * b * x⁻¹) := by group
      rw [h]
      exact Subgroup.mul_mem _ iha ihb
  | inv a ha iha =>
      have hxa : x * a⁻¹ * x⁻¹ = (x * a * x⁻¹)⁻¹ := by group
      rw [hxa]
      exact Subgroup.inv_mem _ iha

theorem closure_normalizes {G : Type*} [Group G] (S T : Set G)
    (hinv : ∀ t ∈ S, t⁻¹ ∈ S)
    (hconj : ∀ t ∈ S, ∀ u ∈ Subgroup.closure T, t * u * t⁻¹ ∈ Subgroup.closure T) :
    ∀ h ∈ Subgroup.closure S, ∀ u ∈ Subgroup.closure T, h * u * h⁻¹ ∈ Subgroup.closure T := by
  intro h hh
  induction hh using Subgroup.closure_induction_left with
  | one =>
      intro u hu
      simpa using hu
  | mul_left x hx y hy ih =>
      intro u hu
      have hcalc : (x * y) * u * (x * y)⁻¹ = x * (y * u * y⁻¹) * x⁻¹ := by
        simp [mul_assoc]
      rw [hcalc]
      exact hconj x hx (y * u * y⁻¹) (ih u hu)
  | inv_mul_cancel x hx y hy ih =>
      intro u hu
      have hcalc : (x⁻¹ * y) * u * (x⁻¹ * y)⁻¹ = x⁻¹ * (y * u * y⁻¹) * x := by
        simp [mul_assoc]
      rw [hcalc]
      have hcalc2 : x⁻¹ * (y * u * y⁻¹) * x = x⁻¹ * (y * u * y⁻¹) * (x⁻¹)⁻¹ := by
        simp
      rw [hcalc2]
      have hx_inv : x⁻¹ ∈ S := hinv x hx
      exact hconj x⁻¹ hx_inv (y * u * y⁻¹) (ih u hu)

theorem solution (c : ℕ → ℕ) :
    ∀ h ∈ GM.torusSub c, ∀ u ∈ GM.unipSub c, h * u * h⁻¹ ∈ GM.unipSub c := by
  intro h hh u hu
  exact closure_normalizes (GM.torusGens c) (GM.unipGens c) (torusGens_inv c)
    (fun t ht => conj_closure_of_gens (GM.unipGens c) t
      (fun g hg => Subgroup.subset_closure (torusGens_conj c t ht g hg))) h hh u hu
