"""Semantic search over the pinned Mathlib (experimental).

Experimental: under evaluation against the baseline of
docs/proposed/mathlib_search.md; interfaces and defaults may change.

Prover sessions search Mathlib with grep and remote search services, which
index other Mathlib versions; on research statements half of their searches
find nothing. This package builds an embedding index of the declarations of
the pinned Mathlib and serves it with the Lean Finder protocol, so the
prover's `lean_leanfinder` tool searches the project's own library
(`LEAN_FINDER_URL`).

1. `extract_declarations.lean`: names, modules, kinds, docstrings (types of
   names absent from the informalized corpus).
2. `build_index.py documents`: joins them with the informal names and
   descriptions of the LeanSearch v2 corpus (Mathlib v4.28.0-rc1).
3. `embed.py`: document embeddings (Qwen3-Embedding-0.6B), on a GPU node
   (`scripts/vista/embed_mathlib_vista.sbatch`).
4. `server.py`: the search service (`aiprover_mathlib_index` unit).

Related published work (tags defined in docs/lit_review/lit_review.md §6):
- [Adopted] Informalized Mathlib corpus and dense retrieval: LeanSearch v2
  (Gao et al., 2026), https://github.com/frenzymath/LeanSearch-v2
- [Similar] Retrieval by user intent, served to provers: Lean Finder (Lu et
  al., ICLR 2026), https://arxiv.org/abs/2510.15940
"""
