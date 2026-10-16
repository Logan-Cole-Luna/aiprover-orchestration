import Mathlib
set_option autoImplicit false

namespace HindmanFSFP

/-- FS(A): the set of all sums over nonempty subsets of A. -/
def FS (A : Finset ℕ) : Finset ℕ :=
  (A.powerset.filter (fun B => B.Nonempty)).image (fun B => ∑ a ∈ B, a)

/-- FP(A): the set of all products over nonempty subsets of A. -/
def FP (A : Finset ℕ) : Finset ℕ :=
  (A.powerset.filter (fun B => B.Nonempty)).image (fun B => ∏ a ∈ B, a)

/-- Theorem 1.1 (Hindman's FS+FP conjecture with growth conditions), stated as a
proposition so that it can be used as a hypothesis. Indices run over `Fin m`, so
`a ⟨0, _⟩` plays the role of `a_1`. -/
def Thm11 : Prop :=
  ∀ (r m : ℕ), 1 ≤ r → 1 ≤ m → ∀ (R D : ℝ), 2 ≤ R → 1 ≤ D →
    ∀ χ : ℕ → Fin r, ∃ (a : Fin m → ℕ) (c : Fin r),
      StrictMono a ∧ (∀ j, 0 < a j) ∧
      (∀ J : Finset (Fin m), J.Nonempty →
        χ (∑ j ∈ J, a j) = c ∧ χ (∏ j ∈ J, a j) = c) ∧
      (∀ d : Fin m, d.val = 0 → R < (a d : ℝ)) ∧
      (∀ d : Fin m, 1 ≤ d.val →
        R * ((((∑ k ∈ Finset.Iio d, a k : ℕ) : ℝ)
              + ((∏ k ∈ Finset.Iio d, a k : ℕ) : ℝ)) ^ D) < (a d : ℝ))

end HindmanFSFP

theorem setting_mem_FS (A : Finset ℕ) (x : ℕ) :
    x ∈ HindmanFSFP.FS A ↔ ∃ B : Finset ℕ, B ⊆ A ∧ B.Nonempty ∧ ∑ a ∈ B, a = x := by
  simp only [HindmanFSFP.FS, Finset.mem_image, Finset.mem_filter, Finset.mem_powerset, and_assoc]

theorem setting_mem_FP (A : Finset ℕ) (x : ℕ) :
    x ∈ HindmanFSFP.FP A ↔ ∃ B : Finset ℕ, B ⊆ A ∧ B.Nonempty ∧ ∏ a ∈ B, a = x := by
  simp only [HindmanFSFP.FP, Finset.mem_image, Finset.mem_filter, Finset.mem_powerset, and_assoc]



theorem hfs_extract (hT : HindmanFSFP.Thm11) (r m : ℕ) (hr : 1 ≤ r) (hm : 1 ≤ m)
    (χ : ℕ → Fin r) :
    ∃ (a : Fin m → ℕ) (c : Fin r), StrictMono a ∧ (∀ j, 0 < a j) ∧
      (∀ J : Finset (Fin m), J.Nonempty → χ (∑ j ∈ J, a j) = c ∧ χ (∏ j ∈ J, a j) = c) ∧
      (∀ d : Fin m, 2 * (∑ k ∈ Finset.Iio d, a k + ∏ k ∈ Finset.Iio d, a k) < a d) := by

  have h := hT r m hr hm (2 : ℝ) (1 : ℝ) (by norm_num) (by norm_num) χ
  rcases h with ⟨a, c, hmono, hpos, hcolor, hfirst, hrest⟩
  refine ⟨a, c, hmono, hpos, hcolor, ?_⟩
  intro d
  by_cases hd0 : d.val = 0
  · 
    have hineq : (2 : ℝ) < (a d : ℝ) := hfirst d hd0
    have hempty : Finset.Iio d = (∅ : Finset (Fin m)) := by
      apply Finset.eq_empty_iff_forall_notMem.mpr
      intro x hx
      have hxval := Finset.mem_Iio.mp hx
      have hxval_lt : x.val < d.val := hxval
      rw [hd0] at hxval_lt
      exact Nat.not_lt_zero _ hxval_lt
    have hsum : (∑ k ∈ Finset.Iio d, a k) = 0 := by simp [hempty]
    have hprod : (∏ k ∈ Finset.Iio d, a k) = 1 := by simp [hempty]
    rw [hsum, hprod]

    have hcast : ((2 : ℕ) : ℝ) < (a d : ℝ) := by simpa using hineq

    simpa [Nat.cast_add, Nat.cast_mul, Nat.cast_one] using (Nat.cast_lt (α := ℝ)).mp hcast
  · 
    have hdpos : 1 ≤ d.val := Nat.one_le_of_lt (Nat.pos_of_ne_zero hd0)
    have hineq : (2 : ℝ) * (((∑ k ∈ Finset.Iio d, a k : ℕ) : ℝ) + ((∏ k ∈ Finset.Iio d, a k : ℕ) : ℝ)) ^ (1 : ℝ) < (a d : ℝ) :=
      hrest d hdpos

    have hineq' : (2 : ℝ) * (((∑ k ∈ Finset.Iio d, a k : ℕ) : ℝ) + ((∏ k ∈ Finset.Iio d, a k : ℕ) : ℝ)) < (a d : ℝ) := by
      simpa [Real.rpow_one] using hineq

    have hcast : ((2 * ((∑ k ∈ Finset.Iio d, a k) + (∏ k ∈ Finset.Iio d, a k)) : ℕ) : ℝ) < (a d : ℝ) := by
      simpa [Nat.cast_add, Nat.cast_mul] using hineq'
    exact (Nat.cast_lt (α := ℝ)).mp hcast

theorem hfs_growth_image (m : ℕ) (a : Fin m → ℕ) (ha : StrictMono a)
    (hg : ∀ d : Fin m, 2 * (∑ k ∈ Finset.Iio d, a k + ∏ k ∈ Finset.Iio d, a k) < a d) :
    ∀ x ∈ Finset.univ.image a,
      2 * (∑ y ∈ (Finset.univ.image a).filter (fun y => y < x), y
        + ∏ y ∈ (Finset.univ.image a).filter (fun y => y < x), y) < x := by
  intro x hx
  rcases Finset.mem_image.1 hx with ⟨d, _, hd⟩
  subst hd
  have h_filter_eq : (Finset.univ.image a).filter (fun y => y < a d) =
      (Finset.Iio d).image a := by
    apply Finset.Subset.antisymm
    · intro y hy
      rcases Finset.mem_filter.1 hy with ⟨hy_img, hy_lt⟩
      rcases Finset.mem_image.1 hy_img with ⟨i, _, hi⟩
      have hi_lt_d : i < d := by
        by_contra! hge
        have hle := ha.monotone hge
        rw [hi] at hle
        linarith
      apply Finset.mem_image.mpr
      exact ⟨i, Finset.mem_Iio.mpr hi_lt_d, hi⟩
    · intro y hy
      rcases Finset.mem_image.1 hy with ⟨i, hi, rfl⟩
      have hi_lt_d : i < d := Finset.mem_Iio.1 hi
      have ha_lt : a i < a d := ha hi_lt_d
      apply Finset.mem_filter.mpr
      exact ⟨Finset.mem_image.mpr ⟨i, Finset.mem_univ _, rfl⟩, ha_lt⟩
  have h_sum_eq : ∑ y ∈ (Finset.univ.image a).filter (fun y => y < a d), y =
      ∑ k ∈ Finset.Iio d, a k := by
    rw [h_filter_eq, Finset.sum_image]
    intro i _ j _ h
    apply ha.injective h
  have h_prod_eq : ∏ y ∈ (Finset.univ.image a).filter (fun y => y < a d), y =
      ∏ k ∈ Finset.Iio d, a k := by
    rw [h_filter_eq, Finset.prod_image]
    intro i _ j _ h
    apply ha.injective h
  rw [h_sum_eq, h_prod_eq]
  exact hg d

theorem hfs_mono_image (m r : ℕ) (a : Fin m → ℕ) (χ : ℕ → Fin r) (c : Fin r)
    (ha : Function.Injective a)
    (hc : ∀ J : Finset (Fin m), J.Nonempty → χ (∑ j ∈ J, a j) = c ∧ χ (∏ j ∈ J, a j) = c) :
    ∀ B ⊆ Finset.univ.image a, B.Nonempty → χ (∑ x ∈ B, x) = c ∧ χ (∏ x ∈ B, x) = c := by
  intro B hBsub hBnon

  let J := Finset.univ.filter fun j : Fin m => a j ∈ B
  have hJnon : J.Nonempty := by
    rcases hBnon with ⟨x, hx⟩
    have hx_image : x ∈ Finset.univ.image a := hBsub hx
    rcases Finset.mem_image.mp hx_image with ⟨j, hj, rfl⟩
    refine ⟨j, Finset.mem_filter.mpr ⟨Finset.mem_univ _, hx⟩⟩
  have h_image_eq : Finset.image a J = B := by
    apply Finset.Subset.antisymm
    · intro x hx
      rcases Finset.mem_image.mp hx with ⟨j, hj, rfl⟩
      rcases Finset.mem_filter.mp hj with ⟨_, hjB⟩
      exact hjB
    · intro x hx
      have hx_image : x ∈ Finset.univ.image a := hBsub hx
      rcases Finset.mem_image.mp hx_image with ⟨j, hj, rfl⟩
      apply Finset.mem_image.mpr
      exact ⟨j, Finset.mem_filter.mpr ⟨Finset.mem_univ _, hx⟩, rfl⟩
  have hsum : ∑ x ∈ B, x = ∑ j ∈ J, a j := by
    calc
      ∑ x ∈ B, x = ∑ x ∈ Finset.image a J, x := by rw [h_image_eq]
      _ = ∑ j ∈ J, a j := by
        refine Finset.sum_image ?_
        intro i hi j hj h
        exact ha h
  have hprod : ∏ x ∈ B, x = ∏ j ∈ J, a j := by
    calc
      ∏ x ∈ B, x = ∏ x ∈ Finset.image a J, x := by rw [h_image_eq]
      _ = ∏ j ∈ J, a j := by
        refine Finset.prod_image ?_
        intro i hi j hj h
        exact ha h
  rcases hc J hJnon with ⟨hsum_c, hprod_c⟩
  rw [hsum, hprod]
  exact ⟨hsum_c, hprod_c⟩

theorem hfs_gt_two (A : Finset ℕ)
    (hA : ∀ x ∈ A, 2 * (∑ y ∈ A.filter (fun y => y < x), y
        + ∏ y ∈ A.filter (fun y => y < x), y) < x) :
    ∀ x ∈ A, 2 < x := by
  intro x hx
  by_cases hne : A.Nonempty
  · have hmin_mem : A.min' hne ∈ A := Finset.min'_mem _ hne
    have hmin_le : ∀ y ∈ A, A.min' hne ≤ y := λ y hy => Finset.min'_le A y hy
    have hfilter_empty : A.filter (fun y => y < A.min' hne) = ∅ := by
      apply Finset.eq_empty_of_forall_notMem
      intro y hy
      rcases Finset.mem_filter.mp hy with ⟨hyA, hylt⟩
      have hle := hmin_le y hyA
      linarith
    have hsum : ∑ y ∈ A.filter (fun y => y < A.min' hne), y = 0 := by
      rw [hfilter_empty]
      simp
    have hprod : ∏ y ∈ A.filter (fun y => y < A.min' hne), y = 1 := by
      rw [hfilter_empty]
      simp
    have hmin_ineq := hA (A.min' hne) hmin_mem
    rw [hsum, hprod] at hmin_ineq

    have h2_lt_min : 2 < A.min' hne := by omega
    have hle_x : A.min' hne ≤ x := hmin_le x hx
    omega
  · 
    exact absurd ⟨x, hx⟩ hne

theorem hfs_below (A : Finset ℕ)
    (hA : ∀ x ∈ A, 2 * (∑ y ∈ A.filter (fun y => y < x), y
        + ∏ y ∈ A.filter (fun y => y < x), y) < x)
    (B : Finset ℕ) (hB : B ⊆ A) (x : ℕ) (hx : x ∈ A) (hlt : ∀ y ∈ B, y < x) :
    2 * (∑ y ∈ B, y + ∏ y ∈ B, y) < x := by
  have hB_filter : B ⊆ A.filter (fun y => y < x) := by
    intro y hy
    exact Finset.mem_filter.mpr ⟨hB hy, hlt y hy⟩
  have hsum_le : ∑ y ∈ B, y ≤ ∑ y ∈ A.filter (fun y => y < x), y :=
    Finset.sum_le_sum_of_subset hB_filter
  have hpos : ∀ y ∈ A, 1 ≤ y := by
    intro y hy
    have h := hA y hy
    have : 0 ≤ ∑ z ∈ A.filter (fun z => z < y), z := Nat.zero_le _
    have hprod_nonneg : 0 ≤ ∏ z ∈ A.filter (fun z => z < y), z := Nat.zero_le _
    have : 2 * (0 + 0) ≤ 2 * (∑ z ∈ A.filter (fun z => z < y), z +
      ∏ z ∈ A.filter (fun z => z < y), z) := by
      nlinarith
    nlinarith
  have hprod_le : ∏ y ∈ B, y ≤ ∏ y ∈ A.filter (fun y => y < x), y :=
    Finset.prod_le_prod_of_subset_of_one_le' hB_filter fun i hi hiB => hpos i (Finset.mem_filter.mp hi).1
  have hsum_prod_le : (∑ y ∈ B, y + ∏ y ∈ B, y) ≤
      (∑ y ∈ A.filter (fun y => y < x), y + ∏ y ∈ A.filter (fun y => y < x), y) :=
    Nat.add_le_add hsum_le hprod_le
  have h_mul_le : 2 * (∑ y ∈ B, y + ∏ y ∈ B, y) ≤
      2 * (∑ y ∈ A.filter (fun y => y < x), y + ∏ y ∈ A.filter (fun y => y < x), y) :=
    Nat.mul_le_mul_left 2 hsum_prod_le
  have h_lt_A : 2 * (∑ y ∈ A.filter (fun y => y < x), y + ∏ y ∈ A.filter (fun y => y < x), y) < x :=
    hA x hx
  exact lt_of_le_of_lt h_mul_le h_lt_A

theorem hfs_le_mem (A : Finset ℕ)
    (hA : ∀ x ∈ A, 2 * (∑ y ∈ A.filter (fun y => y < x), y
        + ∏ y ∈ A.filter (fun y => y < x), y) < x)
    (B : Finset ℕ) (hB : B ⊆ A) (x : ℕ) (hx : x ∈ B) :
    x ≤ ∑ y ∈ B, y ∧ x ≤ ∏ y ∈ B, y := by
  have hx_mem_A : x ∈ A := hB hx
  have hx_pos : 1 ≤ x := by
    by_contra! h
    have hx0 : x = 0 := by omega
    subst hx0
    have h0 := hA 0 hx_mem_A
    have h_empty : A.filter (fun y => y < (0 : ℕ)) = ∅ :=
      Finset.filter_false_of_mem (fun y _ => Nat.not_lt_zero y)
    rw [h_empty] at h0
    simp at h0
  constructor
  · 
    apply Finset.single_le_sum (fun i _ => Nat.zero_le _) hx
  · 
    have hpos_B : ∀ y ∈ B, 1 ≤ y := by
      intro y hy
      have hy_mem_A : y ∈ A := hB hy
      by_contra! h
      have hy0 : y = 0 := by omega
      subst hy0
      have h0 := hA 0 hy_mem_A
      have h_empty : A.filter (fun z => z < (0 : ℕ)) = ∅ :=
        Finset.filter_false_of_mem (fun z _ => Nat.not_lt_zero z)
      rw [h_empty] at h0
      simp at h0
    exact Finset.single_le_prod' (fun i hi => hpos_B i hi) hx

theorem hfs_disj_sum (A : Finset ℕ)
    (hA : ∀ x ∈ A, 2 * (∑ y ∈ A.filter (fun y => y < x), y
        + ∏ y ∈ A.filter (fun y => y < x), y) < x)
    (B C : Finset ℕ) (hB : B ⊆ A) (hC : C ⊆ A) (hd : Disjoint B C)
    (h : ∑ y ∈ B, y = ∑ y ∈ C, y) : B = ∅ ∧ C = ∅ := by
  by_contra! hne

  have hBC_nonempty : (B ∪ C).Nonempty := by
    by_cases hBemp : B = ∅
    · have hC_ne : C ≠ ∅ := hne hBemp
      rcases Finset.nonempty_iff_ne_empty.mpr hC_ne with ⟨x, hx⟩
      exact ⟨x, Finset.mem_union_right B hx⟩
    · rcases Finset.nonempty_iff_ne_empty.mpr hBemp with ⟨x, hx⟩
      exact ⟨x, Finset.mem_union_left C hx⟩
  let j := (B ∪ C).max' hBC_nonempty
  have hj_mem : j ∈ B ∪ C := Finset.max'_mem _ hBC_nonempty
  have hj_max : ∀ y ∈ B ∪ C, y ≤ j := fun y hy => Finset.le_max' (B ∪ C) y hy
  have hjA : j ∈ A := by
    rcases Finset.mem_union.mp hj_mem with (hjB | hjC)
    · exact hB hjB
    · exact hC hjC
  have hgrowth := hA j hjA
  rcases Finset.mem_union.mp hj_mem with (hjB | hjC)
  · 
    have hsum := h
    rw [← Finset.sum_erase_add B (fun x => x) hjB] at hsum
    have hC_lt : ∀ y ∈ C, y < j := by
      intro y hyC
      have hy_mem : y ∈ B ∪ C := Finset.mem_union_right B hyC
      have hle := hj_max y hy_mem
      by_contra! hge
      have heq : y = j := Nat.le_antisymm hle hge
      have hjC : j ∈ C := by rw [← heq]; exact hyC
      exact hd.notMem_of_mem_left_finset hjB hjC
    have h_j_le_sumC : j ≤ ∑ y ∈ C, y := by omega
    have hsumC_le : ∑ y ∈ C, y ≤ ∑ y ∈ A.filter (fun y => y < j), y := by
      refine Finset.sum_le_sum_of_subset ?_
      intro y hy
      have hyC : y ∈ C := hy
      have hyA : y ∈ A := hC hyC
      have hy_lt_j : y < j := hC_lt y hyC
      exact Finset.mem_filter.mpr ⟨hyA, hy_lt_j⟩
    have h2S_lt_j : 2 * (∑ y ∈ A.filter (fun y => y < j), y) < j := by
      have hprod_nonneg : 0 ≤ ∏ y ∈ A.filter (fun y => y < j), y :=
        Finset.prod_nonneg (fun y _ => by omega)
      omega
    have hS_nonneg : 0 ≤ ∑ y ∈ A.filter (fun y => y < j), y :=
      Finset.sum_nonneg (fun y _ => by omega)
    omega
  · 
    have hsum := h
    rw [← Finset.sum_erase_add C (fun x => x) hjC] at hsum
    have hB_lt : ∀ y ∈ B, y < j := by
      intro y hyB
      have hy_mem : y ∈ B ∪ C := Finset.mem_union_left C hyB
      have hle := hj_max y hy_mem
      by_contra! hge
      have heq : y = j := Nat.le_antisymm hle hge
      have hjB : j ∈ B := by rw [← heq]; exact hyB
      exact hd.notMem_of_mem_right_finset hjC hjB
    have h_j_le_sumB : j ≤ ∑ y ∈ B, y := by omega
    have hsumB_le : ∑ y ∈ B, y ≤ ∑ y ∈ A.filter (fun y => y < j), y := by
      refine Finset.sum_le_sum_of_subset ?_
      intro y hy
      have hyB : y ∈ B := hy
      have hyA : y ∈ A := hB hyB
      have hy_lt_j : y < j := hB_lt y hyB
      exact Finset.mem_filter.mpr ⟨hyA, hy_lt_j⟩
    have h2S_lt_j : 2 * (∑ y ∈ A.filter (fun y => y < j), y) < j := by
      have hprod_nonneg : 0 ≤ ∏ y ∈ A.filter (fun y => y < j), y :=
        Finset.prod_nonneg (fun y _ => by omega)
      omega
    have hS_nonneg : 0 ≤ ∑ y ∈ A.filter (fun y => y < j), y :=
      Finset.sum_nonneg (fun y _ => by omega)
    omega

theorem hfs_disj_prod (A : Finset ℕ)
    (hA : ∀ x ∈ A, 2 * (∑ y ∈ A.filter (fun y => y < x), y
        + ∏ y ∈ A.filter (fun y => y < x), y) < x)
    (B C : Finset ℕ) (hB : B ⊆ A) (hC : C ⊆ A) (hd : Disjoint B C)
    (h : ∏ y ∈ B, y = ∏ y ∈ C, y) : B = ∅ ∧ C = ∅ := by

  have h_gt_two : ∀ x ∈ A, 2 < x := by
    intro x hx
    have hx' := hA x hx
    have h_pos : ∀ z ∈ A, 0 < z := by
      intro z hz
      have hz' := hA z hz
      have h_nonneg : 0 ≤ 2 * (∑ y ∈ A.filter (fun y => y < z), y
        + ∏ y ∈ A.filter (fun y => y < z), y) := by omega
      omega
    have h_prod_ge_one : 1 ≤ ∏ y ∈ A.filter (fun y => y < x), y := by
      refine Finset.one_le_prod' fun y hy => ?_
      have hy_mem_A : y ∈ A := (Finset.mem_filter.mp hy).1
      have hy_pos := h_pos y hy_mem_A
      omega
    have h_sum_nonneg : 0 ≤ ∑ y ∈ A.filter (fun y => y < x), y :=
      Finset.sum_nonneg fun y _ => by omega
    have hineq : 2 * 1 ≤ 2 * (∑ y ∈ A.filter (fun y => y < x), y
      + ∏ y ∈ A.filter (fun y => y < x), y) := by
      nlinarith
    omega

  by_cases h_union_empty : B ∪ C = ∅
  · rcases Finset.union_eq_empty.mp h_union_empty with ⟨hB_empty, hC_empty⟩
    exact ⟨hB_empty, hC_empty⟩
  · have h_union_nonempty : (B ∪ C).Nonempty := by
      rwa [Finset.nonempty_iff_ne_empty]
    let m := (B ∪ C).max' h_union_nonempty
    have hm_mem : m ∈ B ∪ C := Finset.max'_mem _ h_union_nonempty
    have h_union_subset_A : B ∪ C ⊆ A := Finset.union_subset hB hC
    have hm_mem_A : m ∈ A := h_union_subset_A hm_mem

    rcases Finset.mem_union.mp hm_mem with (hmB | hmC)
    · 
      have hC_lt_m : ∀ y ∈ C, y < m := by
        intro y hyC
        have hy_mem_union : y ∈ B ∪ C := Finset.mem_union_right B hyC
        have hy_le_m := Finset.le_max' (B ∪ C) y hy_mem_union
        by_contra! hge
        have heq : y = m := by omega
        have hmC' : m ∈ C := heq ▸ hyC

        exact (Finset.disjoint_left.mp hd) hmB hmC'

      have h_m_gt_2prodC : 2 * ∏ y ∈ C, y < m := by
        have hm_A := hA m hm_mem_A
        have hC_subset_filter : C ⊆ A.filter (fun y => y < m) := by
          intro y hy
          refine Finset.mem_filter.mpr ⟨hC hy, hC_lt_m y hy⟩
        have h_prod_C_le : ∏ y ∈ C, y ≤ ∏ y ∈ A.filter (fun y => y < m), y :=
          Finset.prod_le_prod_of_subset_of_one_le' hC_subset_filter fun i hi _hi_not => by
            have hi_mem_A : i ∈ A := (Finset.mem_filter.mp hi).1
            have hi_pos := h_gt_two i hi_mem_A
            omega
        have h_sum_nonneg : 0 ≤ ∑ y ∈ A.filter (fun y => y < m), y :=
          Finset.sum_nonneg fun y _ => by omega
        have h_2prod_lt_m : 2 * ∏ y ∈ A.filter (fun y => y < m), y < m := by
          nlinarith
        nlinarith
      have h_prod_B_ge_m : m ≤ ∏ y ∈ B, y := by
        have h_eq := Finset.mul_prod_erase B (fun y => y) hmB
        have h_rest_ge_one : 1 ≤ ∏ y ∈ B.erase m, y := by
          refine Finset.one_le_prod' fun y hy => ?_
          have hy_mem_B : y ∈ B := Finset.mem_of_mem_erase hy
          have hy_mem_A : y ∈ A := hB hy_mem_B
          have hy_gt_two := h_gt_two y hy_mem_A
          omega
        nlinarith
      have h_prod_C_ge_one : 1 ≤ ∏ y ∈ C, y := by
        refine Finset.one_le_prod' fun y hy => ?_
        have hy_mem_A : y ∈ A := hC hy
        have hy_gt_two := h_gt_two y hy_mem_A
        omega
      have h_contra : ∏ y ∈ B, y > ∏ y ∈ C, y := by
        nlinarith
      rw [h] at h_contra
      exact absurd h_contra (lt_irrefl _)
    · 
      have hB_lt_m : ∀ y ∈ B, y < m := by
        intro y hyB
        have hy_mem_union : y ∈ B ∪ C := Finset.mem_union_left C hyB
        have hy_le_m := Finset.le_max' (B ∪ C) y hy_mem_union
        by_contra! hge
        have heq : y = m := by omega
        have hmB' : m ∈ B := heq ▸ hyB
        exact (Finset.disjoint_left.mp hd) hmB' hmC
      have h_m_gt_2prodB : 2 * ∏ y ∈ B, y < m := by
        have hm_A := hA m hm_mem_A
        have hB_subset_filter : B ⊆ A.filter (fun y => y < m) := by
          intro y hy
          refine Finset.mem_filter.mpr ⟨hB hy, hB_lt_m y hy⟩
        have h_prod_B_le : ∏ y ∈ B, y ≤ ∏ y ∈ A.filter (fun y => y < m), y :=
          Finset.prod_le_prod_of_subset_of_one_le' hB_subset_filter fun i hi _hi_not => by
            have hi_mem_A : i ∈ A := (Finset.mem_filter.mp hi).1
            have hi_pos := h_gt_two i hi_mem_A
            omega
        have h_sum_nonneg : 0 ≤ ∑ y ∈ A.filter (fun y => y < m), y :=
          Finset.sum_nonneg fun y _ => by omega
        have h_2prod_lt_m : 2 * ∏ y ∈ A.filter (fun y => y < m), y < m := by
          nlinarith
        nlinarith
      have h_prod_C_ge_m : m ≤ ∏ y ∈ C, y := by
        have h_eq := Finset.mul_prod_erase C (fun y => y) hmC
        have h_rest_ge_one : 1 ≤ ∏ y ∈ C.erase m, y := by
          refine Finset.one_le_prod' fun y hy => ?_
          have hy_mem_C : y ∈ C := Finset.mem_of_mem_erase hy
          have hy_mem_A : y ∈ A := hC hy_mem_C
          have hy_gt_two := h_gt_two y hy_mem_A
          omega
        nlinarith
      have h_prod_B_ge_one : 1 ≤ ∏ y ∈ B, y := by
        refine Finset.one_le_prod' fun y hy => ?_
        have hy_mem_A : y ∈ A := hB hy
        have hy_gt_two := h_gt_two y hy_mem_A
        omega
      have h_contra : ∏ y ∈ C, y > ∏ y ∈ B, y := by
        nlinarith
      rw [← h] at h_contra
      exact absurd h_contra (lt_irrefl _)

theorem hfs_sum_inj (A : Finset ℕ)
    (hA : ∀ x ∈ A, 2 * (∑ y ∈ A.filter (fun y => y < x), y
        + ∏ y ∈ A.filter (fun y => y < x), y) < x)
    (B C : Finset ℕ) (hB : B ⊆ A) (hC : C ⊆ A)
    (h : ∑ y ∈ B, y = ∑ y ∈ C, y) : B = C := by
  by_contra! hne

  have h_symm_nonempty : (B \ C).Nonempty ∨ (C \ B).Nonempty := by
    by_contra! hboth


    have hBC_empty : B \ C = ∅ := Finset.not_nonempty_iff_eq_empty.mp hboth.1
    have hCB_empty : C \ B = ∅ := Finset.not_nonempty_iff_eq_empty.mp hboth.2
    have hBC : B ⊆ C := by
      intro x hx
      by_contra! hx2
      have : x ∈ B \ C := Finset.mem_sdiff.mpr ⟨hx, hx2⟩
      rw [hBC_empty] at this
      simp at this
    have hCB : C ⊆ B := by
      intro x hx
      by_contra! hx2
      have : x ∈ C \ B := Finset.mem_sdiff.mpr ⟨hx, hx2⟩
      rw [hCB_empty] at this
      simp at this
    exact hne (Finset.Subset.antisymm hBC hCB)

  let S := (B \ C) ∪ (C \ B)
  have hS_nonempty : S.Nonempty :=
    Finset.union_nonempty.mpr h_symm_nonempty
  let m := S.max' hS_nonempty
  have hm_mem : m ∈ S := Finset.max'_mem _ hS_nonempty
  have hmA : m ∈ A := by
    rcases Finset.mem_union.mp hm_mem with (hm' | hm')
    · exact hB (Finset.mem_sdiff.mp hm').1
    · exact hC (Finset.mem_sdiff.mp hm').1

  have hineq := hA m hmA


  have hS_le_m : ∀ x ∈ S, x ≤ m := by
    intro x hx
    exact Finset.le_max' S x hx

  have hsum_diff : ∑ y ∈ B \ C, y = ∑ y ∈ C \ B, y := by
    have hsumB := Finset.sum_sdiff (f := id) (Finset.inter_subset_left (s₁ := B) (s₂ := C))

    have hBsdiff : B \ (B ∩ C) = B \ C := by
      ext x; simp
    have hsumC := Finset.sum_sdiff (f := id) (Finset.inter_subset_right (s₁ := B) (s₂ := C))
    have hCsdiff : C \ (B ∩ C) = C \ B := by
      ext x; simp
    rw [hBsdiff] at hsumB
    rw [hCsdiff] at hsumC
    have hsum_eq : ∑ x ∈ B \ C, id x = ∑ x ∈ C \ B, id x := by
      apply add_right_cancel
      calc
        ∑ x ∈ B \ C, id x + ∑ x ∈ B ∩ C, id x = ∑ x ∈ B, id x := hsumB
        _ = ∑ x ∈ C, id x := by simpa using h
        _ = ∑ x ∈ C \ B, id x + ∑ x ∈ B ∩ C, id x := by symm; exact hsumC
    simpa using hsum_eq

  rcases Finset.mem_union.mp hm_mem with (hmB | hmC)
  · 


    have hCB_lt_m : ∀ y ∈ C \ B, y < m := by
      intro y hy
      have hyS : y ∈ S := Finset.mem_union_right _ hy
      have hyle := hS_le_m y hyS
      by_contra! hge
      have heq : y = m := by omega
      subst heq

      have hm_notin_B : m ∉ B := (Finset.mem_sdiff.mp hy).2
      have hm_in_B : m ∈ B := (Finset.mem_sdiff.mp hmB).1
      exact hm_notin_B hm_in_B

    have hCB_sub_A : C \ B ⊆ A := by
      intro y hy
      exact hC (Finset.mem_sdiff.mp hy).1
    have hCB_sub_filter : C \ B ⊆ A.filter (fun y => y < m) := by
      intro y hy
      apply Finset.mem_filter.mpr
      exact ⟨hCB_sub_A hy, hCB_lt_m y hy⟩

    have hsum_le : ∑ y ∈ C \ B, y ≤ ∑ y ∈ A.filter (fun y => y < m), y :=
      Finset.sum_le_sum_of_subset hCB_sub_filter


    have h2S_lt_m : 2 * (∑ y ∈ A.filter (fun y => y < m), y) < m := by
      have hpos_prod : 0 ≤ ∏ y ∈ A.filter (fun y => y < m), y :=
        Finset.prod_nonneg (fun y _ => by omega)
      omega

    have hm_le_sumBC : m ≤ ∑ y ∈ B \ C, y := by
      have := Finset.single_le_sum (fun y hy => Nat.zero_le y) hmB
      simpa using this


    rw [hsum_diff] at hm_le_sumBC
    have hm_le_S : m ≤ ∑ y ∈ A.filter (fun y => y < m), y := by omega
    have hm_pos : 0 < m := by
      by_contra! hm0
      have hm0' : m = 0 := by omega
      rw [hm0'] at hineq
      have hfilter_empty : A.filter (fun z => z < (0 : ℕ)) = ∅ :=
        Finset.filter_false _
      rw [hfilter_empty] at hineq
      simp at hineq
    omega
  · 

    have hBC_lt_m : ∀ y ∈ B \ C, y < m := by
      intro y hy
      have hyS : y ∈ S := Finset.mem_union_left _ hy
      have hyle := hS_le_m y hyS
      by_contra! hge
      have heq : y = m := by omega
      subst heq
      have hm_notin_C : m ∉ C := (Finset.mem_sdiff.mp hy).2
      have hm_in_C : m ∈ C := (Finset.mem_sdiff.mp hmC).1
      exact hm_notin_C hm_in_C

    have hBC_sub_A : B \ C ⊆ A := by
      intro y hy
      exact hB (Finset.mem_sdiff.mp hy).1
    have hBC_sub_filter : B \ C ⊆ A.filter (fun y => y < m) := by
      intro y hy
      apply Finset.mem_filter.mpr
      exact ⟨hBC_sub_A hy, hBC_lt_m y hy⟩

    have hsum_le : ∑ y ∈ B \ C, y ≤ ∑ y ∈ A.filter (fun y => y < m), y :=
      Finset.sum_le_sum_of_subset hBC_sub_filter

    have h2S_lt_m : 2 * (∑ y ∈ A.filter (fun y => y < m), y) < m := by
      have hpos_prod : 0 ≤ ∏ y ∈ A.filter (fun y => y < m), y :=
        Finset.prod_nonneg (fun y _ => by omega)
      omega

    have hm_le_sumCB : m ≤ ∑ y ∈ C \ B, y := by
      have := Finset.single_le_sum (fun y hy => Nat.zero_le y) hmC
      simpa using this


    rw [← hsum_diff] at hm_le_sumCB
    have hm_le_S : m ≤ ∑ y ∈ A.filter (fun y => y < m), y := by omega
    have hm_pos : 0 < m := by
      by_contra! hm0
      have hm0' : m = 0 := by omega
      rw [hm0'] at hineq
      have hfilter_empty : A.filter (fun z => z < (0 : ℕ)) = ∅ :=
        Finset.filter_false _
      rw [hfilter_empty] at hineq
      simp at hineq
    omega

theorem hfs_prod_inj (A : Finset ℕ)
    (hA : ∀ x ∈ A, 2 * (∑ y ∈ A.filter (fun y => y < x), y
        + ∏ y ∈ A.filter (fun y => y < x), y) < x)
    (B C : Finset ℕ) (hB : B ⊆ A) (hC : C ⊆ A)
    (h : ∏ y ∈ B, y = ∏ y ∈ C, y) : B = C := by
  have hD1 : B ∩ C ⊆ B := fun x hx => (Finset.mem_inter.mp hx).1
  have hD2 : B ∩ C ⊆ C := fun x hx => (Finset.mem_inter.mp hx).2
  have hpB : (∏ y ∈ B \ (B ∩ C), y) * ∏ y ∈ B ∩ C, y = ∏ y ∈ B, y :=
    Finset.prod_sdiff hD1
  have hpC : (∏ y ∈ C \ (B ∩ C), y) * ∏ y ∈ B ∩ C, y = ∏ y ∈ C, y :=
    Finset.prod_sdiff hD2
  have hpos : 0 < ∏ y ∈ B ∩ C, y := by
    apply Finset.prod_pos
    intro y hy
    have := hfs_gt_two A hA y (hB (hD1 hy))
    omega
  have heq : (∏ y ∈ B \ (B ∩ C), y) = ∏ y ∈ C \ (B ∩ C), y := by
    apply Nat.eq_of_mul_eq_mul_right hpos
    rw [hpB, hpC]
    exact h
  have hdisj : Disjoint (B \ (B ∩ C)) (C \ (B ∩ C)) := by
    rw [Finset.disjoint_left]
    intro x hx1 hx2
    rw [Finset.mem_sdiff, Finset.mem_inter] at hx1 hx2
    exact hx1.2 ⟨hx1.1, hx2.1⟩
  obtain ⟨h1, h2⟩ := hfs_disj_prod A hA _ _
    (fun x hx => hB (Finset.mem_sdiff.mp hx).1)
    (fun x hx => hC (Finset.mem_sdiff.mp hx).1) hdisj heq
  have hBC : B ⊆ C := by
    intro x hx
    by_contra hxC
    have hm : x ∈ B \ (B ∩ C) :=
      Finset.mem_sdiff.mpr ⟨hx, fun hm => hxC (Finset.mem_inter.mp hm).2⟩
    rw [h1] at hm
    exact absurd hm (Finset.notMem_empty x)
  have hCB : C ⊆ B := by
    intro x hx
    by_contra hxB
    have hm : x ∈ C \ (B ∩ C) :=
      Finset.mem_sdiff.mpr ⟨hx, fun hm => hxB (Finset.mem_inter.mp hm).1⟩
    rw [h2] at hm
    exact absurd hm (Finset.notMem_empty x)
  exact Finset.Subset.antisymm hBC hCB

theorem hfs_sum_eq_prod (A : Finset ℕ)
    (hA : ∀ x ∈ A, 2 * (∑ y ∈ A.filter (fun y => y < x), y
        + ∏ y ∈ A.filter (fun y => y < x), y) < x)
    (B C : Finset ℕ) (hB : B ⊆ A) (hC : C ⊆ A) (hBne : B.Nonempty) (hCne : C.Nonempty)
    (h : ∑ y ∈ B, y = ∏ y ∈ C, y) : ∑ y ∈ B, y ∈ A := by
  have h2 := hfs_gt_two A hA
  obtain ⟨b, hbB, hbmax⟩ := Finset.exists_max_image B id hBne
  obtain ⟨c, hcC, hcmax⟩ := Finset.exists_max_image C id hCne
  have hbmax' : ∀ y ∈ B, y ≤ b := fun y hy => hbmax y hy
  have hcmax' : ∀ y ∈ C, y ≤ c := fun y hy => hcmax y hy
  have hbA : b ∈ A := hB hbB
  have hcA : c ∈ A := hC hcC
  have hbS : b ≤ ∑ y ∈ B, y := (hfs_le_mem A hA B hB b hbB).1
  have hcS : c ≤ ∏ y ∈ C, y := (hfs_le_mem A hA C hC c hcC).2
  rcases lt_trichotomy b c with hlt | heq | hgt
  · exfalso
    have hh := hfs_below A hA B hB c hcA (fun y hy => lt_of_le_of_lt (hbmax' y hy) hlt)
    omega
  · have hbC : b ∈ C := by rw [heq]; exact hcC
    have hcmax'' : ∀ y ∈ C, y ≤ b := by
      intro y hy
      have := hcmax' y hy
      omega
    have hB'sub : B.erase b ⊆ A := (Finset.erase_subset _ _).trans hB
    have hlt' : ∀ y ∈ B.erase b, y < b := fun y hy =>
      lt_of_le_of_ne (hbmax' y (Finset.mem_of_mem_erase hy)) (Finset.ne_of_mem_erase hy)
    have hbelow := hfs_below A hA (B.erase b) hB'sub b hbA hlt'
    have hsumB : ∑ y ∈ B.erase b, y + b = ∑ y ∈ B, y :=
      Finset.sum_erase_add B (fun y => y) hbB
    have hprodC : (∏ y ∈ C.erase b, y) * b = ∏ y ∈ C, y :=
      Finset.prod_erase_mul C (fun y => y) hbC
    have hP : 0 < ∏ y ∈ C.erase b, y :=
      Finset.prod_pos (fun y hy => by
        have := h2 y (hC (Finset.mem_of_mem_erase hy))
        omega)
    by_cases hP1 : ∏ y ∈ C.erase b, y = 1
    · have hSb : ∑ y ∈ B, y = b := by
        rw [h, ← hprodC, hP1, one_mul]
      rw [hSb]
      exact hbA
    · exfalso
      have hge : 2 * b ≤ (∏ y ∈ C.erase b, y) * b :=
        Nat.mul_le_mul_right b (by omega)
      omega
  · exfalso
    have hh := hfs_below A hA C hC b hbA (fun y hy => lt_of_le_of_lt (hcmax' y hy) hgt)
    omega

theorem hfs_card_nonempty (A : Finset ℕ) :
    (A.powerset.filter (fun B => B.Nonempty)).card = 2 ^ A.card - 1 := by
  have h_total : A.powerset.card = 2 ^ A.card := Finset.card_powerset _
  have h_mem : ∅ ∈ A.powerset := by
    apply Finset.mem_powerset.mpr
    exact Finset.empty_subset _
  have h_empty_not_nonempty : ¬ (∅ : Finset ℕ).Nonempty := Finset.not_nonempty_empty
  have h_filter_eq : A.powerset.filter (fun B => B.Nonempty) = A.powerset.erase ∅ := by
    ext B
    constructor
    · intro h
      have hB_mem : B ∈ A.powerset := (Finset.mem_filter.mp h).1
      have hB_nonempty : B.Nonempty := (Finset.mem_filter.mp h).2
      apply Finset.mem_erase.mpr
      constructor
      · intro h_eq; rw [h_eq] at hB_nonempty; exact Finset.not_nonempty_empty hB_nonempty
      · exact hB_mem
    · intro h
      have hB_mem : B ∈ A.powerset := (Finset.mem_erase.mp h).2
      have hB_ne : B ≠ ∅ := (Finset.mem_erase.mp h).1
      apply Finset.mem_filter.mpr
      constructor
      · exact hB_mem
      · by_contra h_not; apply hB_ne; exact (Finset.not_nonempty_iff_eq_empty.mp h_not)
  have h_card_erase : (A.powerset.erase ∅).card = A.powerset.card - 1 := by
    apply Finset.card_erase_of_mem h_mem
  rw [h_filter_eq, h_card_erase, h_total]

theorem hfs_FS_card (A : Finset ℕ)
    (hA : ∀ x ∈ A, 2 * (∑ y ∈ A.filter (fun y => y < x), y
        + ∏ y ∈ A.filter (fun y => y < x), y) < x) :
    (HindmanFSFP.FS A).card = 2 ^ A.card - 1 := by
  have hcard_subsets : (A.powerset.filter (fun B => B.Nonempty)).card = 2 ^ A.card - 1 :=
    hfs_card_nonempty A
  have h_inj_on : ∀ (B C : Finset ℕ),
    B ∈ (A.powerset.filter (fun B => B.Nonempty)) →
    C ∈ (A.powerset.filter (fun B => B.Nonempty)) →
    (∑ a ∈ B, a) = (∑ a ∈ C, a) → B = C := by
    intro B C hB hC hsum
    rcases Finset.mem_filter.mp hB with ⟨hB_pow, hB_ne⟩
    rcases Finset.mem_filter.mp hC with ⟨hC_pow, hC_ne⟩
    have hB_sub : B ⊆ A := Finset.mem_powerset.mp hB_pow
    have hC_sub : C ⊆ A := Finset.mem_powerset.mp hC_pow
    exact hfs_sum_inj A hA B C hB_sub hC_sub hsum
  have hcard_eq : (HindmanFSFP.FS A).card = (A.powerset.filter (fun B => B.Nonempty)).card := by
    rw [HindmanFSFP.FS]
    apply Finset.card_image_of_injOn
    intro B hB C hC hsum
    exact h_inj_on B C hB hC hsum
  calc
    (HindmanFSFP.FS A).card = (A.powerset.filter (fun B => B.Nonempty)).card := hcard_eq
    _ = 2 ^ A.card - 1 := hcard_subsets

theorem hfs_FP_card (A : Finset ℕ)
    (hA : ∀ x ∈ A, 2 * (∑ y ∈ A.filter (fun y => y < x), y
        + ∏ y ∈ A.filter (fun y => y < x), y) < x) :
    (HindmanFSFP.FP A).card = 2 ^ A.card - 1 := by
  let S := A.powerset.filter (fun B => B.Nonempty)
  have hFP_eq : HindmanFSFP.FP A = S.image (fun B => ∏ a ∈ B, a) := by
    ext x
    simp [HindmanFSFP.FP, S, Finset.mem_image, Finset.mem_filter, Finset.mem_powerset]
  rw [hFP_eq]
  have h_card_S : S.card = 2 ^ A.card - 1 := by
    have h_empty_mem : (∅ : Finset ℕ) ∈ A.powerset := by simp
    have hS_eq : S = (A.powerset).erase ∅ := by
      ext B
      simp only [S, Finset.mem_filter, Finset.mem_powerset, Finset.mem_erase]
      rw [Finset.nonempty_iff_ne_empty]
      exact and_comm
    rw [hS_eq]
    rw [Finset.card_erase_of_mem h_empty_mem, Finset.card_powerset]
  have h_inj : Set.InjOn (fun (B : Finset ℕ) => ∏ a ∈ B, a) (S : Set (Finset ℕ)) := by
    intro B hB C hC hprod
    have hB_mem : B ∈ S := hB
    have hC_mem : C ∈ S := hC
    have hB_mem_filter : B ∈ A.powerset.filter (fun B => B.Nonempty) := by simpa [S] using hB_mem
    have hC_mem_filter : C ∈ A.powerset.filter (fun B => B.Nonempty) := by simpa [S] using hC_mem
    have hB_sub : B ⊆ A := Finset.mem_powerset.mp (Finset.mem_filter.mp hB_mem_filter).left
    have hC_sub : C ⊆ A := Finset.mem_powerset.mp (Finset.mem_filter.mp hC_mem_filter).left
    exact hfs_prod_inj A hA B C hB_sub hC_sub hprod
  rw [Finset.card_image_of_injOn h_inj, h_card_S]

theorem hfs_inter (A : Finset ℕ)
    (hA : ∀ x ∈ A, 2 * (∑ y ∈ A.filter (fun y => y < x), y
        + ∏ y ∈ A.filter (fun y => y < x), y) < x) :
    HindmanFSFP.FS A ∩ HindmanFSFP.FP A = A := by
  apply Finset.Subset.antisymm
  · intro x hx
    rcases Finset.mem_inter.mp hx with ⟨hxFS, hxFP⟩
    rw [setting_mem_FS] at hxFS
    rw [setting_mem_FP] at hxFP
    rcases hxFS with ⟨B, hB, hBne, hsum⟩
    rcases hxFP with ⟨C, hC, hCne, hprod⟩
    have hsum_eq_prod : ∑ y ∈ B, y = ∏ y ∈ C, y := by rw [hsum, hprod]
    have hx_in_A : ∑ y ∈ B, y ∈ A :=
      hfs_sum_eq_prod A hA B C hB hC hBne hCne hsum_eq_prod
    rw [← hsum]
    exact hx_in_A
  · intro x hx
    have hsing_nonempty : ({x} : Finset ℕ).Nonempty := by
      refine ⟨x, Finset.mem_singleton.mpr rfl⟩
    have hsum_sing : ∑ y ∈ ({x} : Finset ℕ), y = x := by simp
    have hprod_sing : ∏ y ∈ ({x} : Finset ℕ), y = x := by simp
    have hxFS : x ∈ HindmanFSFP.FS A := by
      rw [setting_mem_FS]
      refine ⟨{ x }, ?_, hsing_nonempty, hsum_sing⟩
      apply Finset.singleton_subset_iff.mpr
      exact hx
    have hxFP : x ∈ HindmanFSFP.FP A := by
      rw [setting_mem_FP]
      refine ⟨{ x }, ?_, hsing_nonempty, hprod_sing⟩
      apply Finset.singleton_subset_iff.mpr
      exact hx
    exact Finset.mem_inter.mpr ⟨hxFS, hxFP⟩

theorem solution (hT : HindmanFSFP.Thm11)
    (r m : ℕ) (hr : 1 ≤ r) (hm : 1 ≤ m) (χ : ℕ → Fin r) :
    ∃ A : Finset ℕ, A.card = m ∧ (∀ a ∈ A, 0 < a) ∧
      (∃ c : Fin r, ∀ x ∈ HindmanFSFP.FS A ∪ HindmanFSFP.FP A, χ x = c) ∧
      (HindmanFSFP.FS A).card = 2 ^ m - 1 ∧ (HindmanFSFP.FP A).card = 2 ^ m - 1 ∧
      HindmanFSFP.FS A ∩ HindmanFSFP.FP A = A := by
  obtain ⟨a, c, ha, hpos, hc, hg⟩ := hfs_extract hT r m hr hm χ
  have hinj : Function.Injective a := ha.injective
  have hA := hfs_growth_image m a ha hg
  have hcard : (Finset.univ.image a).card = m := by
    rw [Finset.card_image_of_injective _ hinj, Finset.card_univ, Fintype.card_fin]
  refine ⟨Finset.univ.image a, hcard, ?_, ⟨c, ?_⟩, ?_, ?_, hfs_inter _ hA⟩
  · intro x hx
    obtain ⟨j, -, rfl⟩ := Finset.mem_image.mp hx
    exact hpos j
  · intro x hx
    rcases Finset.mem_union.mp hx with h | h
    · obtain ⟨B, hB, hne, rfl⟩ := (setting_mem_FS _ _).mp h
      exact (hfs_mono_image m r a χ c hinj hc B hB hne).1
    · obtain ⟨B, hB, hne, rfl⟩ := (setting_mem_FP _ _).mp h
      exact (hfs_mono_image m r a χ c hinj hc B hB hne).2
  · rw [hfs_FS_card _ hA, hcard]
  · rw [hfs_FP_card _ hA, hcard]
