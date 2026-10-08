import Definitions.Def_gsimple_g_m_perfect



theorem mk_rel_one (c : ℕ → ℕ) (r : GM.F c) (h : GM.Rel c r) :
    PresentedGroup.mk (GM.Rels c) r = 1 := by
  apply PresentedGroup.one_of_mem
  exact h

theorem mk_eqr (c : ℕ → ℕ) (a b : GM.F c) (h : GM.Rel c (GM.eqr a b)) :
    PresentedGroup.mk (GM.Rels c) a = PresentedGroup.mk (GM.Rels c) b := by
  exact (PresentedGroup.mk_eq_mk_of_mul_inv_mem h)

theorem subgroup_top_of_gens (c : ℕ → ℕ) (N : Subgroup (GM.Grp c))
    (h : ∀ g : GM.Gen c, (PresentedGroup.of g : GM.Grp c) ∈ N) : N = ⊤ := by
  have hgen : Set.range (PresentedGroup.of (α := GM.Gen c) (rels := GM.Rels c) : GM.Gen c → GM.Grp c) ⊆ N.carrier := by
    rintro _ ⟨g, rfl⟩
    exact h g
  have hclosure : Subgroup.closure (Set.range (PresentedGroup.of (α := GM.Gen c) (rels := GM.Rels c) : GM.Gen c → GM.Grp c)) ≤ N :=
    ((Subgroup.closure_le (K := N)).mpr hgen)
  have htop : Subgroup.closure (Set.range (PresentedGroup.of (α := GM.Gen c) (rels := GM.Rels c) : GM.Gen c → GM.Grp c)) = ⊤ :=
    PresentedGroup.closure_range_of (GM.Rels c)
  rw [htop] at hclosure
  exact top_le_iff.mp hclosure

theorem cm_mem_commutator (c : ℕ → ℕ) (a b : GM.F c) :
    PresentedGroup.mk (GM.Rels c) (GM.cm a b) ∈ commutator (GM.Grp c) := by
  have h : PresentedGroup.mk (GM.Rels c) (GM.cm a b) = ⁅PresentedGroup.mk (GM.Rels c) a,
    PresentedGroup.mk (GM.Rels c) b⁆ := by
    simp [GM.cm, commutatorElement_def]
  rw [h]
  exact Subgroup.commutator_mem_commutator (Subgroup.mem_top _) (Subgroup.mem_top _)

theorem xm_mem (c : ℕ → ℕ) (hc : 1 ≤ c 2) (u : ℂ) :
    PresentedGroup.mk (GM.Rels c) (GM.xm u) ∈ commutator (GM.Grp c) := by
  set G := GM.Grp c
  let m := PresentedGroup.mk (GM.Rels c)
  have hvalid1 : GM.Valid c 1 2 1 := by
    dsimp [GM.Valid]
    exact ⟨by decide, by decide, hc⟩
  have hvalid0 : GM.Valid c 0 2 1 := by
    dsimp [GM.Valid]
    exact ⟨by decide, by decide, hc⟩
  have hrel : GM.Rel c (GM.eqr (GM.cm (GM.xx 1 2 1 hvalid1 (u : ℂ)) (GM.yy 0 2 1 hvalid0 1)) (GM.xm ((u : ℂ) * 1))) :=
    GM.Rel.U3a 1 hvalid1 hvalid0 (u : ℂ) 1
  have h_eq : m (GM.cm (GM.xx 1 2 1 hvalid1 (u : ℂ)) (GM.yy 0 2 1 hvalid0 1)) = m (GM.xm ((u : ℂ) * 1)) := by
    apply mk_eqr c _ _ hrel
  have h_cm_mem : m (GM.cm (GM.xx 1 2 1 hvalid1 (u : ℂ)) (GM.yy 0 2 1 hvalid0 1)) ∈ commutator G := by
    rw [commutator_eq_closure G]
    apply Subgroup.subset_closure
    exact commutator_mem_commutatorSet (m (GM.xx 1 2 1 hvalid1 (u : ℂ))) (m (GM.yy 0 2 1 hvalid0 1))
  have h_target : m (GM.xm u) = m (GM.xm ((u : ℂ) * 1)) := by simp
  rw [h_target]
  rw [← h_eq]
  exact h_cm_mem

theorem ym_mem (c : ℕ → ℕ) (hc : 1 ≤ c 2) (u : ℂ) :
    PresentedGroup.mk (GM.Rels c) (GM.ym u) ∈ commutator (GM.Grp c) := by
  have hvalid0 : GM.Valid c 0 2 1 := by
    refine ⟨by decide, by decide, ?_⟩
    exact Nat.le_trans (by decide) hc
  have hvalid1 : GM.Valid c 1 2 1 := by
    refine ⟨by decide, by decide, ?_⟩
    exact Nat.le_trans (by decide) hc
  have h_rel : GM.Rel c (GM.eqr (GM.cm (GM.xx 0 2 1 hvalid0 u) (GM.yy 1 2 1 hvalid1 1))
    (GM.ym (u * 1))) :=
    GM.Rel.U3b 1 hvalid0 hvalid1 u 1
  have h_eq : PresentedGroup.mk (GM.Rels c) (GM.cm (GM.xx 0 2 1 hvalid0 u) (GM.yy 1 2 1 hvalid1 1)) =
    PresentedGroup.mk (GM.Rels c) (GM.ym (u * 1)) :=
    mk_eqr c _ _ h_rel
  have h_cm_mem : PresentedGroup.mk (GM.Rels c)
    (GM.cm (GM.xx 0 2 1 hvalid0 u) (GM.yy 1 2 1 hvalid1 1)) ∈ commutator (GM.Grp c) :=
    cm_mem_commutator c _ _
  rw [h_eq] at h_cm_mem
  simpa [mul_one] using h_cm_mem

theorem wm_mem (c : ℕ → ℕ) (hc : 1 ≤ c 2) (s : ℂˣ) :
    PresentedGroup.mk (GM.Rels c) (GM.wm s) ∈ commutator (GM.Grp c) := by
  have hx : PresentedGroup.mk (GM.Rels c) (GM.xm (s : ℂ)) ∈ commutator (GM.Grp c) :=
    xm_mem c hc (s : ℂ)
  have hy : PresentedGroup.mk (GM.Rels c) (GM.ym (-((s : ℂ)⁻¹))) ∈ commutator (GM.Grp c) :=
    ym_mem c hc (-((s : ℂ)⁻¹))
  have h_eq : PresentedGroup.mk (GM.Rels c) (GM.wm s) =
      PresentedGroup.mk (GM.Rels c) (GM.xm (s : ℂ)) *
      PresentedGroup.mk (GM.Rels c) (GM.ym (-((s : ℂ)⁻¹))) *
      PresentedGroup.mk (GM.Rels c) (GM.xm (s : ℂ)) := by
    simp [GM.wm, GM.xm, GM.ym, map_mul]
  rw [h_eq]
  exact Subgroup.mul_mem _ (Subgroup.mul_mem _ hx hy) hx

theorem exists_unit_pow_ne_one (d : ℕ) (hd : 0 < d) :
    ∃ s : ℂˣ, (s : ℂ) ^ d ≠ 1 := by
  have h2val : (2 : ℂ) ^ d ≠ 1 := by
    intro h
    have hnormSq : Complex.normSq ((2 : ℂ) ^ d) = Complex.normSq (1 : ℂ) := by rw [h]
    have h_one : Complex.normSq (1 : ℂ) = 1 := by simp
    rw [h_one] at hnormSq
    have hcalc : Complex.normSq ((2 : ℂ) ^ d) = (Complex.normSq (2 : ℂ)) ^ d := by
      simpa using map_pow Complex.normSq (2 : ℂ) d
    have h4 : Complex.normSq (2 : ℂ) = (4 : ℝ) := by norm_num
    rw [hcalc, h4] at hnormSq

    have h4pos : (0 : ℝ) < 4 := by norm_num
    have h4dpos : 0 < (4 : ℝ) ^ d := pow_pos h4pos d
    have h4dgt1 : (1 : ℝ) < (4 : ℝ) ^ d := by
      have h_nat : 1 < (4 : ℕ) ^ d := Nat.one_lt_pow (by omega : d ≠ 0) (by norm_num : 1 < 4)
      exact_mod_cast h_nat
    linarith
  refine ⟨Units.mk0 (2 : ℂ) (by norm_num), h2val⟩

theorem xx_add_mk (c : ℕ → ℕ) (l j k : ℕ) (h : GM.Valid c l j k) (u v : ℂ) :
    PresentedGroup.mk (GM.Rels c) (GM.xx l j k h (u + v)) =
      PresentedGroup.mk (GM.Rels c) (GM.xx l j k h u) *
        PresentedGroup.mk (GM.Rels c) (GM.xx l j k h v) := by
  have hrel : GM.Rel c (GM.eqr (GM.xx l j k h u * GM.xx l j k h v) (GM.xx l j k h (u + v))) :=
    GM.Rel.Im1a l j k h u v
  have h_eq := mk_eqr c (GM.xx l j k h u * GM.xx l j k h v) (GM.xx l j k h (u + v)) hrel
  calc
    PresentedGroup.mk (GM.Rels c) (GM.xx l j k h (u + v))
        = PresentedGroup.mk (GM.Rels c) (GM.xx l j k h u * GM.xx l j k h v) := by
      rw [← h_eq]
    _ = PresentedGroup.mk (GM.Rels c) (GM.xx l j k h u) *
        PresentedGroup.mk (GM.Rels c) (GM.xx l j k h v) := by
      rw [map_mul]

theorem yy_add_mk (c : ℕ → ℕ) (l j k : ℕ) (h : GM.Valid c l j k) (u v : ℂ) :
    PresentedGroup.mk (GM.Rels c) (GM.yy l j k h (u + v)) =
      PresentedGroup.mk (GM.Rels c) (GM.yy l j k h u) *
        PresentedGroup.mk (GM.Rels c) (GM.yy l j k h v) := by
  have hrel : GM.Rel c (GM.eqr (GM.yy l j k h u * GM.yy l j k h v) (GM.yy l j k h (u + v))) :=
    GM.Rel.Im1b l j k h u v
  have := mk_eqr c (GM.yy l j k h u * GM.yy l j k h v) (GM.yy l j k h (u + v)) hrel
  simpa [GM.eqr, mul_inv_rev] using this.symm

theorem xx_sub_mk (c : ℕ → ℕ) (l j k : ℕ) (h : GM.Valid c l j k) (a b : ℂ) :
    PresentedGroup.mk (GM.Rels c) (GM.xx l j k h (a - b)) =
      PresentedGroup.mk (GM.Rels c) (GM.xx l j k h a) *
        (PresentedGroup.mk (GM.Rels c) (GM.xx l j k h b))⁻¹ := by
  have hzero : PresentedGroup.mk (GM.Rels c) (GM.xx l j k h (0 : ℂ)) = 1 := by
    have hrel : GM.Rel c (GM.eqr (GM.xx l j k h (0 : ℂ)) 1) := by
      have := GM.Rel.Im1a l j k h (0 : ℂ) (0 : ℂ)
      simpa [GM.eqr] using this
    have hmem : GM.eqr (GM.xx l j k h (0 : ℂ)) 1 ∈ GM.Rels c := hrel
    exact PresentedGroup.one_of_mem hmem
  have hneg (u : ℂ) : PresentedGroup.mk (GM.Rels c) (GM.xx l j k h (-u)) =
      (PresentedGroup.mk (GM.Rels c) (GM.xx l j k h u))⁻¹ := by
    have h := xx_add_mk c l j k h u (-u)

    have hzero' : (u + (-u : ℂ)) = (0 : ℂ) := by ring
    rw [hzero'] at h
    rw [hzero] at h



    exact eq_inv_of_mul_eq_one_right h.symm
  calc
    PresentedGroup.mk (GM.Rels c) (GM.xx l j k h (a - b))
        = PresentedGroup.mk (GM.Rels c) (GM.xx l j k h (a + (-b))) := by ring
    _ = PresentedGroup.mk (GM.Rels c) (GM.xx l j k h a) *
        PresentedGroup.mk (GM.Rels c) (GM.xx l j k h (-b)) := xx_add_mk c l j k h a (-b)
    _ = PresentedGroup.mk (GM.Rels c) (GM.xx l j k h a) *
        (PresentedGroup.mk (GM.Rels c) (GM.xx l j k h b))⁻¹ := by rw [hneg b]

theorem yy_sub_mk (c : ℕ → ℕ) (l j k : ℕ) (h : GM.Valid c l j k) (a b : ℂ) :
    PresentedGroup.mk (GM.Rels c) (GM.yy l j k h (a - b)) =
      PresentedGroup.mk (GM.Rels c) (GM.yy l j k h a) *
        (PresentedGroup.mk (GM.Rels c) (GM.yy l j k h b))⁻¹ := by
  have hRel : GM.Rel c (GM.eqr (GM.yy l j k h (a - b) * GM.yy l j k h b) (GM.yy l j k h a)) := by
    have := GM.Rel.Im1b l j k h (a - b) b
    simpa [sub_add_cancel] using this
  have hEq := mk_eqr c (GM.yy l j k h (a - b) * GM.yy l j k h b) (GM.yy l j k h a) hRel


  have hMul : PresentedGroup.mk (GM.Rels c) (GM.yy l j k h (a - b) * GM.yy l j k h b) =
      PresentedGroup.mk (GM.Rels c) (GM.yy l j k h (a - b)) *
      PresentedGroup.mk (GM.Rels c) (GM.yy l j k h b) := by
    simp
  rw [hMul] at hEq


  calc
    PresentedGroup.mk (GM.Rels c) (GM.yy l j k h (a - b))
        = (PresentedGroup.mk (GM.Rels c) (GM.yy l j k h (a - b)) *
          PresentedGroup.mk (GM.Rels c) (GM.yy l j k h b)) *
          (PresentedGroup.mk (GM.Rels c) (GM.yy l j k h b))⁻¹ := by
      simp
    _ = PresentedGroup.mk (GM.Rels c) (GM.yy l j k h a) *
        (PresentedGroup.mk (GM.Rels c) (GM.yy l j k h b))⁻¹ := by
      rw [hEq]

theorem xx_conj_mem (c : ℕ → ℕ) (l j k : ℕ) (h : GM.Valid c l j k) (s : ℂˣ) (v : ℂ) :
    PresentedGroup.mk (GM.Rels c) (GM.xx l j k h ((s : ℂ) ^ (j - l) * v)) *
      (PresentedGroup.mk (GM.Rels c) (GM.xx l j k h v))⁻¹ ∈ commutator (GM.Grp c) := by
  have him6b := GM.Rel.Im6b l j k h s v
  have hmem : GM.eqr (GM.h2 s * GM.xx l j k h v * (GM.h2 s)⁻¹) (GM.xx l j k h ((s : ℂ) ^ (j - l) * v)) ∈ GM.Rels c := him6b
  have heq := PresentedGroup.mk_eq_mk_of_mul_inv_mem hmem
  rw [← heq]
  simpa [map_mul, map_inv, commutatorElement_def] using Subgroup.commutator_mem_commutator
    (g₁ := PresentedGroup.mk (GM.Rels c) (GM.h2 s))
    (g₂ := PresentedGroup.mk (GM.Rels c) (GM.xx l j k h v))
    (h₁ := show PresentedGroup.mk (GM.Rels c) (GM.h2 s) ∈ (⊤ : Subgroup (GM.Grp c)) from Subgroup.mem_top _)
    (h₂ := show PresentedGroup.mk (GM.Rels c) (GM.xx l j k h v) ∈ (⊤ : Subgroup (GM.Grp c)) from Subgroup.mem_top _)

open GM in
theorem yy_conj_mem_aiprover_s0 (c : ℕ → ℕ) (l j k : ℕ) (h : GM.Valid c l j k) (s : ℂˣ) (v : ℂ) :
    PresentedGroup.mk (GM.Rels c) (GM.yy l j k h (((s : ℂ) ^ (j - l))⁻¹ * v)) *
      (PresentedGroup.mk (GM.Rels c) (GM.yy l j k h v))⁻¹ ∈ commutator (GM.Grp c) := by
  have h_rel := Rel.Im6d l j k h s v
  have h_eq := mk_eqr c (GM.h2 s * GM.yy l j k h v * (GM.h2 s)⁻¹)
    (GM.yy l j k h (((s : ℂ) ^ (j - l))⁻¹ * v)) h_rel
  have h_mk : PresentedGroup.mk (GM.Rels c) (GM.yy l j k h (((s : ℂ) ^ (j - l))⁻¹ * v)) =
      (PresentedGroup.mk (GM.Rels c) (GM.h2 s) : GM.Grp c) *
      (PresentedGroup.mk (GM.Rels c) (GM.yy l j k h v) : GM.Grp c) *
      (PresentedGroup.mk (GM.Rels c) (GM.h2 s) : GM.Grp c)⁻¹ := by
    calc
      PresentedGroup.mk (GM.Rels c) (GM.yy l j k h (((s : ℂ) ^ (j - l))⁻¹ * v)) =
          PresentedGroup.mk (GM.Rels c) (GM.h2 s * GM.yy l j k h v * (GM.h2 s)⁻¹) := h_eq.symm
      _ = (PresentedGroup.mk (GM.Rels c) (GM.h2 s) : GM.Grp c) *
          (PresentedGroup.mk (GM.Rels c) (GM.yy l j k h v) : GM.Grp c) *
          (PresentedGroup.mk (GM.Rels c) (GM.h2 s) : GM.Grp c)⁻¹ := by simp [map_mul]
  rw [h_mk]
  have h_comm : (PresentedGroup.mk (GM.Rels c) (GM.h2 s) : GM.Grp c) *
      (PresentedGroup.mk (GM.Rels c) (GM.yy l j k h v) : GM.Grp c) *
      (PresentedGroup.mk (GM.Rels c) (GM.h2 s) : GM.Grp c)⁻¹ *
      (PresentedGroup.mk (GM.Rels c) (GM.yy l j k h v) : GM.Grp c)⁻¹ =
      ⁅(PresentedGroup.mk (GM.Rels c) (GM.h2 s) : GM.Grp c),
        (PresentedGroup.mk (GM.Rels c) (GM.yy l j k h v) : GM.Grp c)⁆ := rfl
  rw [h_comm]
  dsimp [commutator]
  apply Subgroup.subset_closure
  exact ⟨(PresentedGroup.mk (GM.Rels c) (GM.h2 s) : GM.Grp c), Subgroup.mem_top _, 
    (PresentedGroup.mk (GM.Rels c) (GM.yy l j k h v) : GM.Grp c), Subgroup.mem_top _, rfl⟩

theorem yy_conj_mem (c : ℕ → ℕ) (l j k : ℕ) (h : GM.Valid c l j k) (s : ℂˣ) (v : ℂ) :
    PresentedGroup.mk (GM.Rels c) (GM.yy l j k h (((s : ℂ) ^ (j - l))⁻¹ * v)) *
      (PresentedGroup.mk (GM.Rels c) (GM.yy l j k h v))⁻¹ ∈ commutator (GM.Grp c) := by
  first
    | exact yy_conj_mem_aiprover_s0
    | (intros; apply yy_conj_mem_aiprover_s0 <;> assumption)

theorem xx_mem (c : ℕ → ℕ) (l j k : ℕ) (h : GM.Valid c l j k) (u : ℂ) :
    PresentedGroup.mk (GM.Rels c) (GM.xx l j k h u) ∈ commutator (GM.Grp c) := by
  have hpos_or : j - l = 0 ∨ 0 < j - l := Nat.eq_zero_or_pos (j - l)
  rcases hpos_or with (hjl | hdpos)
  · rcases h with ⟨hlt, _, _⟩
    have : l < l :=
      Nat.lt_of_lt_of_le hlt (Nat.le_of_sub_eq_zero hjl)
    exact absurd this (Nat.lt_irrefl _)
  · rcases exists_unit_pow_ne_one (j - l) hdpos with ⟨s, hs⟩
    have hden_ne_zero : (s : ℂ) ^ (j - l) - 1 ≠ 0 := by
      intro hzero
      have h_one : (s : ℂ) ^ (j - l) = 1 := sub_eq_zero.mp hzero
      exact hs h_one
    set v := u / ((s : ℂ) ^ (j - l) - 1) with hv_def
    have h_eq : u = ((s : ℂ) ^ (j - l) - 1) * v := by
      dsimp [v]
      field_simp [hden_ne_zero]
    rw [h_eq]
    have : ((s : ℂ) ^ (j - l) - 1) * v = ((s : ℂ) ^ (j - l)) * v - v := by ring
    rw [this]
    rw [xx_sub_mk c l j k h (((s : ℂ) ^ (j - l)) * v) v]
    exact xx_conj_mem c l j k h s v

theorem yy_mem (c : ℕ → ℕ) (l j k : ℕ) (h : GM.Valid c l j k) (u : ℂ) :
    PresentedGroup.mk (GM.Rels c) (GM.yy l j k h u) ∈ commutator (GM.Grp c) := by
  have hjl : 0 < j - l := by
    have := h.1
    omega
  obtain ⟨s, hs⟩ := exists_unit_pow_ne_one (j - l) hjl
  have ht : ((s : ℂ) ^ (j - l))⁻¹ - 1 ≠ 0 := by
    intro h0
    apply hs
    have h1 : ((s : ℂ) ^ (j - l))⁻¹ = 1 := sub_eq_zero.mp h0
    first
      | exact inv_eq_one.mp h1
      | simpa using h1
  have hu : ((s : ℂ) ^ (j - l))⁻¹ * (u / (((s : ℂ) ^ (j - l))⁻¹ - 1)) -
      u / (((s : ℂ) ^ (j - l))⁻¹ - 1) = u := by
    rw [← sub_one_mul]
    first
      | exact mul_div_cancel₀ u ht
      | (rw [mul_comm]; exact div_mul_cancel₀ u ht)
      | field_simp
  have key := yy_conj_mem c l j k h s (u / (((s : ℂ) ^ (j - l))⁻¹ - 1))
  first
    | (rw [← yy_sub_mk c l j k h, hu] at key; exact key)
    | (rw [← hu, yy_sub_mk c l j k h]; exact key)

theorem ww_mem (c : ℕ → ℕ) (l j k : ℕ) (h : GM.Valid c l j k) (s : ℂˣ) :
    PresentedGroup.mk (GM.Rels c) (GM.ww l j k h s) ∈ commutator (GM.Grp c) := by
  let f : GM.F c →* GM.Grp c := PresentedGroup.mk (GM.Rels c)
  have hxx : f (GM.xx l j k h (s : ℂ)) ∈ commutator (GM.Grp c) := by
    dsimp [f]
    simpa using xx_mem c l j k h (s : ℂ)
  have hyy : f (GM.yy l j k h (-((s : ℂ)⁻¹) / GM.cc l j)) ∈ commutator (GM.Grp c) := by
    simpa using yy_mem c l j k h (-((s : ℂ)⁻¹) / GM.cc l j)

  have hprod : f (GM.xx l j k h (s : ℂ) * GM.yy l j k h (-((s : ℂ)⁻¹) / GM.cc l j) * GM.xx l j k h (s : ℂ)) ∈ commutator (GM.Grp c) := by
    have hcalc : f (GM.xx l j k h (s : ℂ) * GM.yy l j k h (-((s : ℂ)⁻¹) / GM.cc l j) * GM.xx l j k h (s : ℂ)) =
        f (GM.xx l j k h (s : ℂ)) * f (GM.yy l j k h (-((s : ℂ)⁻¹) / GM.cc l j)) * f (GM.xx l j k h (s : ℂ)) := by
      simp [map_mul]
    rw [hcalc]
    exact Subgroup.mul_mem _ (Subgroup.mul_mem _ hxx hyy) hxx

  have hww : GM.ww l j k h s = GM.xx l j k h (s : ℂ) * GM.yy l j k h (-((s : ℂ)⁻¹) / GM.cc l j) * GM.xx l j k h (s : ℂ) := rfl
  rw [hww]
  exact hprod

theorem h1h2_neg_mem (c : ℕ → ℕ) (hc : 1 ≤ c 2) (s : ℂˣ) :
    PresentedGroup.mk (GM.Rels c) (GM.h1 (-s) * GM.h2 (-(s⁻¹))) ∈ commutator (GM.Grp c) := by
  have hrel : GM.Rel c (GM.eqr (GM.wm s * GM.wm 1) (GM.h1 (-s) * GM.h2 (-(s⁻¹)))) :=
    GM.Rel.Re3 s
  have h_eq : PresentedGroup.mk (GM.Rels c) (GM.wm s * GM.wm 1) =
      PresentedGroup.mk (GM.Rels c) (GM.h1 (-s) * GM.h2 (-(s⁻¹))) :=
    mk_eqr c (GM.wm s * GM.wm 1) (GM.h1 (-s) * GM.h2 (-(s⁻¹))) hrel
  have h_wm_s : PresentedGroup.mk (GM.Rels c) (GM.wm s) ∈ commutator (GM.Grp c) :=
    wm_mem c hc s
  have h_wm_1 : PresentedGroup.mk (GM.Rels c) (GM.wm 1) ∈ commutator (GM.Grp c) :=
    wm_mem c hc 1
  have h_prod : PresentedGroup.mk (GM.Rels c) (GM.wm s * GM.wm 1) ∈ commutator (GM.Grp c) :=
    Subgroup.mul_mem _ h_wm_s h_wm_1
  rw [← h_eq]
  exact h_prod

theorem h1h2_inv_mem (c : ℕ → ℕ) (hc : 1 ≤ c 2) (x : ℂˣ) :
    PresentedGroup.mk (GM.Rels c) (GM.h1 x * GM.h2 x⁻¹) ∈ commutator (GM.Grp c) := by
  have hrel := GM.Rel.Re3 (c := c) (-x)
  have h_simplified : (GM.h1 (c := c) (-(-x)) * GM.h2 (c := c) (-((-x)⁻¹))) = (GM.h1 (c := c) x * GM.h2 (c := c) x⁻¹) := by
    simp
  have hmem : PresentedGroup.mk (GM.Rels c) (GM.wm (-x) * GM.wm 1) ∈ commutator (GM.Grp c) := by
    have h1 : PresentedGroup.mk (GM.Rels c) (GM.wm (-x)) ∈ commutator (GM.Grp c) := wm_mem c hc (-x)
    have h2 : PresentedGroup.mk (GM.Rels c) (GM.wm 1) ∈ commutator (GM.Grp c) := wm_mem c hc 1
    exact Subgroup.mul_mem _ h1 h2
  have heq := mk_eqr c (GM.wm (-x) * GM.wm 1) (GM.h1 x * GM.h2 x⁻¹) (by
    simpa [h_simplified] using hrel)
  rw [← heq]
  exact hmem

theorem im3_mem (c : ℕ → ℕ) (l j k : ℕ) (h : GM.Valid c l j k) (s : ℂˣ) :
    PresentedGroup.mk (GM.Rels c) (GM.h1 ((-s) ^ (j - l)) * GM.h2 ((-s) ^ (l + 1)))
      ∈ commutator (GM.Grp c) := by
  set w1 := GM.ww l j k h (s ^ ((l + 1) * (j - l))) with hw1
  set w0 := GM.ww l j k h 1 with hw0
  have hIm3_rel : GM.Rel c (GM.eqr (w1 * w0) (GM.h1 ((-s) ^ (j - l)) * GM.h2 ((-s) ^ (l + 1)))) :=
    GM.Rel.Im3 l j k h s
  have h_mul_inv : w1 * w0 * (GM.h1 ((-s) ^ (j - l)) * GM.h2 ((-s) ^ (l + 1)))⁻¹ ∈ GM.Rels c := by
    simpa [GM.Rels] using hIm3_rel
  have h_mem' : PresentedGroup.mk (GM.Rels c) (GM.eqr (w1 * w0) (GM.h1 ((-s) ^ (j - l)) * GM.h2 ((-s) ^ (l + 1)))) = 1 :=
    PresentedGroup.one_of_mem hIm3_rel
  have h_eq : PresentedGroup.mk (GM.Rels c) (w1 * w0) =
             PresentedGroup.mk (GM.Rels c) (GM.h1 ((-s) ^ (j - l)) * GM.h2 ((-s) ^ (l + 1))) := by
    apply PresentedGroup.mk_eq_mk_of_mul_inv_mem h_mul_inv
  have h_prod_mem : PresentedGroup.mk (GM.Rels c) (w1 * w0) ∈ commutator (GM.Grp c) := by
    have h1 : PresentedGroup.mk (GM.Rels c) w1 ∈ commutator (GM.Grp c) :=
      ww_mem c l j k h (s ^ ((l + 1) * (j - l)))
    have h2 : PresentedGroup.mk (GM.Rels c) w0 ∈ commutator (GM.Grp c) :=
      ww_mem c l j k h 1
    exact Subgroup.mul_mem (commutator (GM.Grp c)) h1 h2
  rw [← h_eq]
  exact h_prod_mem

theorem h1sq_h2_mem (c : ℕ → ℕ) (hc : 1 ≤ c 2) (y : ℂˣ) :
    PresentedGroup.mk (GM.Rels c) (GM.h1 (y ^ 2) * GM.h2 y) ∈ commutator (GM.Grp c) := by
  have hvalid : GM.Valid c 0 2 1 := ⟨by decide, by decide, hc⟩
  have hw1 := ww_mem c 0 2 1 hvalid (y ^ 2)
  have hw2 := ww_mem c 0 2 1 hvalid 1
  have hprod : PresentedGroup.mk (GM.Rels c)
      (GM.ww 0 2 1 hvalid (y ^ 2) * GM.ww 0 2 1 hvalid 1) ∈ commutator (GM.Grp c) :=
    (commutator (GM.Grp c)).mul_mem hw1 hw2
  have him3 : GM.Rel c (GM.eqr (GM.ww 0 2 1 hvalid (y ^ 2) * GM.ww 0 2 1 hvalid 1)
      (GM.h1 (y ^ 2) * GM.h2 y)) := by
    have := GM.Rel.Im3 0 2 1 hvalid (-y)
    simpa [show ((-y : ℂˣ) : ℂ) ^ ((0+1)*(2-0)) = (y : ℂ)^2 by norm_num] using this
  have heq : PresentedGroup.mk (GM.Rels c)
      (GM.ww 0 2 1 hvalid (y ^ 2) * GM.ww 0 2 1 hvalid 1) =
      PresentedGroup.mk (GM.Rels c) (GM.h1 (y ^ 2) * GM.h2 y) := by
    simpa [GM.Rels] using PresentedGroup.mk_eq_mk_of_mul_inv_mem him3
  rw [← heq]
  exact hprod

theorem h1_cube_eq (c : ℕ → ℕ) (z : ℂˣ) :
    PresentedGroup.mk (GM.Rels c) (GM.h1 (z ^ 3)) =
      PresentedGroup.mk (GM.Rels c) (GM.h1 z * GM.h2 z⁻¹) *
        (PresentedGroup.mk (GM.Rels c) (GM.h1 ((z⁻¹) ^ 2) * GM.h2 z⁻¹))⁻¹ := by
  let φ := PresentedGroup.mk (GM.Rels c)
  have hφ_mul : ∀ x y : GM.F c, φ (x * y) = φ x * φ y := by
    intro x y; exact MonoidHom.map_mul φ x y
  have hφ_inv : ∀ x : GM.F c, φ (x⁻¹) = (φ x)⁻¹ := by
    intro x; exact MonoidHom.map_inv φ x





  have h1_mul : ∀ s t : ℂˣ, φ (GM.h1 (c := c) (s * t)) = φ (GM.h1 (c := c) s) * φ (GM.h1 (c := c) t) := by
    intro s t
    have hRel : GM.Rel c (GM.eqr (GM.h1 (c := c) s * GM.h1 (c := c) t) (GM.h1 (c := c) (s * t))) :=
      GM.Rel.H1a (c := c) s t
    have h_one := PresentedGroup.one_of_mem (hx := hRel) (rels := GM.Rels c)


    have hcalc : φ (GM.eqr (GM.h1 (c := c) s * GM.h1 (c := c) t) (GM.h1 (c := c) (s * t))) =
      φ (GM.h1 (c := c) s * GM.h1 (c := c) t) * (φ (GM.h1 (c := c) (s * t)))⁻¹ := by
      simp [GM.eqr, hφ_mul, hφ_inv]
    rw [hcalc] at h_one



    have h_eq : φ (GM.h1 (c := c) s * GM.h1 (c := c) t) = φ (GM.h1 (c := c) (s * t)) := by
      calc
        φ (GM.h1 (c := c) s * GM.h1 (c := c) t) =
          (φ (GM.h1 (c := c) s * GM.h1 (c := c) t) * (φ (GM.h1 (c := c) (s * t)))⁻¹) *
          φ (GM.h1 (c := c) (s * t)) := by group
        _ = 1 * φ (GM.h1 (c := c) (s * t)) := by rw [h_one]
        _ = φ (GM.h1 (c := c) (s * t)) := by simp
    rw [hφ_mul] at h_eq
    exact h_eq.symm


  have h1_one : φ (GM.h1 (c := c) 1) = 1 := by
    have hRel : GM.Rel c (GM.eqr (GM.h1 (c := c) 1 * GM.h1 (c := c) 1) (GM.h1 (c := c) (1 * 1))) :=
      GM.Rel.H1a (c := c) 1 1
    have h_one := PresentedGroup.one_of_mem (hx := hRel) (rels := GM.Rels c)


    have hcalc : φ (GM.eqr (GM.h1 (c := c) 1 * GM.h1 (c := c) 1) (GM.h1 (c := c) (1 * 1))) =
      φ (GM.h1 (c := c) 1 * GM.h1 (c := c) 1) * (φ (GM.h1 (c := c) (1 * 1)))⁻¹ := by
      simp [GM.eqr, hφ_mul]
    rw [hcalc] at h_one


    rw [mul_one] at h_one

    rw [hφ_mul] at h_one


    calc
      φ (GM.h1 (c := c) 1) = (φ (GM.h1 (c := c) 1) * φ (GM.h1 (c := c) 1)) * (φ (GM.h1 (c := c) 1))⁻¹ := by group
      _ = 1 := h_one


  have h1_inv : ∀ s : ℂˣ, φ (GM.h1 (c := c) s⁻¹) = (φ (GM.h1 (c := c) s))⁻¹ := by
    intro s
    have hRel : GM.Rel c (GM.eqr (GM.h1 (c := c) s * GM.h1 (c := c) s⁻¹) (GM.h1 (c := c) (s * s⁻¹))) :=
      GM.Rel.H1a (c := c) s s⁻¹

    have h_one := PresentedGroup.one_of_mem (hx := hRel) (rels := GM.Rels c)
    have hcalc : φ (GM.eqr (GM.h1 (c := c) s * GM.h1 (c := c) s⁻¹) (GM.h1 (c := c) (s * s⁻¹))) =
      φ (GM.h1 (c := c) s * GM.h1 (c := c) s⁻¹) * (φ (GM.h1 (c := c) (s * s⁻¹)))⁻¹ := by
      simp [GM.eqr, hφ_mul, hφ_inv]
    rw [hcalc] at h_one


    have h_s_one : φ (GM.h1 (c := c) (s * s⁻¹)) = 1 := by
      have : s * s⁻¹ = (1 : ℂˣ) := by simp
      rw [this]
      exact h1_one
    rw [h_s_one] at h_one



    have : (1 : PresentedGroup (GM.Rels c))⁻¹ = 1 := by simp
    rw [this, mul_one] at h_one


    calc
      φ (GM.h1 (c := c) s⁻¹) = 1 * φ (GM.h1 (c := c) s⁻¹) := by simp
      _ = ((φ (GM.h1 (c := c) s))⁻¹ * φ (GM.h1 (c := c) s)) * φ (GM.h1 (c := c) s⁻¹) := by simp
      _ = (φ (GM.h1 (c := c) s))⁻¹ * (φ (GM.h1 (c := c) s) * φ (GM.h1 (c := c) s⁻¹)) := by group
      _ = (φ (GM.h1 (c := c) s))⁻¹ * 1 := by
        rw [show φ (GM.h1 (c := c) s) * φ (GM.h1 (c := c) s⁻¹) = 1 from h_one]
      _ = (φ (GM.h1 (c := c) s))⁻¹ := by simp







  have h_pow_h1 : ∀ (x : ℂˣ) (n : ℕ), φ (GM.h1 (c := c) (x ^ n)) = (φ (GM.h1 (c := c) x)) ^ n := by
    intro x n
    induction' n with k ih
    · 
      simp [h1_one]
    · 
      rw [pow_succ, pow_succ]

      rw [h1_mul (x ^ k) x]
      rw [ih]



  have h_inv_h1 : ∀ (x : ℂˣ), φ (GM.h1 (c := c) x⁻¹) = (φ (GM.h1 (c := c) x))⁻¹ := h1_inv

  calc
    φ (GM.h1 (c := c) (z ^ 3)) = (φ (GM.h1 (c := c) z)) ^ 3 := by rw [h_pow_h1]
    _ = (φ (GM.h1 (c := c) z)) * ((φ (GM.h1 (c := c) z))⁻¹ ^ 2)⁻¹ := by group
    _ = (φ (GM.h1 (c := c) z) * φ (GM.h2 (c := c) z⁻¹)) * (((φ (GM.h1 (c := c) z))⁻¹ ^ 2) * φ (GM.h2 (c := c) z⁻¹))⁻¹ := by group
    _ = (φ (GM.h1 (c := c) z) * φ (GM.h2 (c := c) z⁻¹)) * ((φ (GM.h1 (c := c) (z⁻¹ ^ 2)) * φ (GM.h2 (c := c) z⁻¹))⁻¹) := by
      simp [h_pow_h1, h_inv_h1]
    _ = PresentedGroup.mk (GM.Rels c) (GM.h1 z * GM.h2 z⁻¹) *
        (PresentedGroup.mk (GM.Rels c) (GM.h1 ((z⁻¹) ^ 2) * GM.h2 z⁻¹))⁻¹ := by
      simp [φ, hφ_mul]

theorem exists_unit_cube (s : ℂˣ) : ∃ z : ℂˣ, z ^ 3 = s := by
  obtain ⟨w, hw⟩ := IsAlgClosed.exists_pow_nat_eq (s : ℂ) (by decide : 0 < 3)
  have hw_ne_zero : w ≠ 0 := by
    intro hzero
    rw [hzero, zero_pow (by decide : 3 ≠ 0)] at hw
    have hs_ne_zero : (s : ℂ) ≠ 0 := Units.ne_zero s
    exact hs_ne_zero (Eq.symm hw)
  let z : ℂˣ := Units.mk0 w hw_ne_zero
  refine ⟨z, ?_⟩
  apply Units.ext_iff.mpr
  calc
    (z : ℂ) ^ 3 = w ^ 3 := rfl
    _ = (s : ℂ) := hw

theorem h1_mem (c : ℕ → ℕ) (hc : 1 ≤ c 2) (s : ℂˣ) :
    PresentedGroup.mk (GM.Rels c) (GM.h1 s) ∈ commutator (GM.Grp c) := by
  rcases exists_unit_cube s with ⟨z, hz⟩

  have h_comm_z : PresentedGroup.mk (GM.Rels c) (GM.h1 z * GM.h2 (z⁻¹)) ∈ commutator (GM.Grp c) :=
    h1h2_inv_mem c hc z

  have h_comm_z_inv : PresentedGroup.mk (GM.Rels c) (GM.h1 ((z⁻¹) ^ 2) * GM.h2 (z⁻¹)) ∈
    commutator (GM.Grp c) :=
    h1sq_h2_mem c hc (z⁻¹)

  have h_cube := h1_cube_eq c z


  have h_prod : PresentedGroup.mk (GM.Rels c) (GM.h1 z * GM.h2 (z⁻¹)) *
    (PresentedGroup.mk (GM.Rels c) (GM.h1 ((z⁻¹) ^ 2) * GM.h2 (z⁻¹)))⁻¹ ∈
    commutator (GM.Grp c) := by
    apply Subgroup.mul_mem
    · exact h_comm_z
    · apply Subgroup.inv_mem
      exact h_comm_z_inv



  have h_cube_mem : PresentedGroup.mk (GM.Rels c) (GM.h1 (z ^ 3)) ∈ commutator (GM.Grp c) := by
    rw [h_cube]
    exact h_prod

  simpa [hz] using h_cube_mem

theorem h2_eq_conj (c : ℕ → ℕ) (s : ℂˣ) :
    PresentedGroup.mk (GM.Rels c) (GM.h2 s) =
      PresentedGroup.mk (GM.Rels c) (GM.wm 1) * PresentedGroup.mk (GM.Rels c) (GM.h1 s) *
        (PresentedGroup.mk (GM.Rels c) (GM.wm 1))⁻¹ := by

  have h_eq := mk_eqr c (GM.wm 1 * GM.h1 s * (GM.wm 1)⁻¹) (GM.h2 s) (by
    apply GM.Rel.Re5a)

  simpa [PresentedGroup.mk, map_mul, map_inv] using h_eq.symm

theorem h2_mem (c : ℕ → ℕ) (hc : 1 ≤ c 2) (s : ℂˣ) :
    PresentedGroup.mk (GM.Rels c) (GM.h2 s) ∈ commutator (GM.Grp c) := by
  have hnormal : (commutator (GM.Grp c)).Normal := inferInstance
  have hh1 : PresentedGroup.mk (GM.Rels c) (GM.h1 s) ∈ commutator (GM.Grp c) := h1_mem c hc s
  have hww : PresentedGroup.mk (GM.Rels c) (GM.wm 1) ∈ commutator (GM.Grp c) := wm_mem c hc 1
  rw [h2_eq_conj c s]
  exact hnormal.conj_mem (PresentedGroup.mk (GM.Rels c) (GM.h1 s)) hh1
    (PresentedGroup.mk (GM.Rels c) (GM.wm 1))

theorem solution (c : ℕ → ℕ) (hc : 1 ≤ c 2) :
    commutator (GM.Grp c) = ⊤ := by
  apply subgroup_top_of_gens
  intro g
  cases g with
  | H1 s => exact h1_mem c hc s
  | H2 s => exact h2_mem c hc s
  | Xm u => exact xm_mem c hc u
  | Ym u => exact ym_mem c hc u
  | X l j k h u => exact xx_mem c l j k h u
  | Y l j k h u => exact yy_mem c l j k h u
