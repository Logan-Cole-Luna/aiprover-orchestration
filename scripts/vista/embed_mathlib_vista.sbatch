#!/bin/bash -l
#SBATCH -J aiprover_embed
#SBATCH -p gh-dev
#SBATCH -N 1
#SBATCH --ntasks-per-node=1
#SBATCH -t 00:45:00
#SBATCH -A CDA25016
#
# Embeddings of the Mathlib index documents (aiprover_orchestration/search),
# on one GH200 in the serving container. Usage, from a Vista login node, with
# documents.jsonl, embed.py and the Qwen3-Embedding-0.6B checkpoint in DIR:
#   sbatch --export=ALL,DIR=$WORK/mathlib_index \
#       scripts/vista/embed_mathlib_vista.sbatch
# Output: $DIR/embeddings.npy (float16, one L2-normalized row per document).
set -uo pipefail
set +u; module load tacc-apptainer/1.4.1; set -u

: "${DIR:?set DIR to the index directory}"
SIF=${SIF:-$WORK/containers/vllm-gh200.sif}
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# Triton locates libcuda through Apptainer's bind mount (vllm_common.sh).
export TRITON_LIBCUDA_PATH=/.singularity.d/libs
export CC=/usr/bin/gcc CXX=/usr/bin/g++
echo "dir=$DIR $(date -Is)"
apptainer exec --nv --bind "$DIR" "$SIF" python3 -u "$DIR/embed.py" \
    "$DIR/qwen3-embedding-0.6b" "$DIR/documents.jsonl" "$DIR/embeddings.npy"
echo "done $(date -Is)"
