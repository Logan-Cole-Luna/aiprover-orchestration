#!/bin/bash -l
#SBATCH -J aiprover_srv_s3
#SBATCH -p h100
#SBATCH -N 1
#SBATCH --ntasks-per-node=1
#SBATCH -t 48:00:00
#
# AIProver model server on one TACC Stampede3 GPU node (4 GPUs, NVLink).
#
# Tensor parallelism across the node's GPUs: each layer's all-reduce stays on
# NVLink, so no Ray cluster or inter-node hop is involved, and every GPU works
# on every token (Vista's two-node pipeline runs its stages one after the
# other). The FP8 checkpoint (112 GiB) occupies 28 GiB per GPU at TP=4.
#
# The command is AIProver/AIProver_plugin/serve/serve_aiprover.sh (Mistral
# config, loader, tokenizer, tool and reasoning parsers) with the decode
# settings of vllm_args.sh. Clients locate the server through $HANDOFF
# (`node=<n> port=<p> job=<id> model=<name>`), written before the weights
# load; readiness is established by polling /v1/models.
#
#   sbatch -A TG-CIS260432 scripts/serve_aiprover_s3.sbatch
#
# Environment (all optional):
#   MODEL_DIR    checkpoint, Mistral native format [/work/11428/pjana/aiprover_model]
#   VLLM_VENV    x86 venv with vllm               [$WORK/venvs/vllm-0.27.1]
#   SCRIPTS_DIR  serve_aiprover.sh, vllm_args.sh  [/work/11757/loganluna/vista/aiprover_serve/scripts]
#   HANDOFF      handoff file                     [$SCRATCH/servers/aiprover_server_s3.txt]
#   SERVED_NAME [aiprover], PORT [8060], TP [GPUs on the node],
#   MAX_MODEL_LEN [1048576], the settings of vllm_args.sh, EXTRA_ARGS.
set -uo pipefail

export MODEL_DIR=${MODEL_DIR:-/work/11428/pjana/aiprover_model}
VLLM_VENV=${VLLM_VENV:-$WORK/venvs/vllm-0.27.1}
SCRIPTS_DIR=${SCRIPTS_DIR:-/work/11757/loganluna/vista/aiprover_serve/scripts}
HANDOFF=${HANDOFF:-$SCRATCH/servers/aiprover_server_s3.txt}
export SERVED_NAME=${SERVED_NAME:-aiprover} PORT=${PORT:-8060}
JOBLOGS=$SCRATCH/joblogs
mkdir -p "$(dirname "$HANDOFF")" "$JOBLOGS"

source "$VLLM_VENV/bin/activate"
source "$SCRIPTS_DIR/vllm_args.sh"
# Compute nodes read the checkpoint from disk only; compile caches are
# node-local.
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export VLLM_CACHE_ROOT=/tmp/$USER/vllm_$SLURM_JOB_ID
export TRITON_CACHE_DIR=/tmp/$USER/triton_$SLURM_JOB_ID
MOE_CONFIG_DIR=${MOE_CONFIG_DIR:-$WORK/aiprover_serve/moe_configs}
[ -d "$MOE_CONFIG_DIR" ] && export VLLM_TUNED_CONFIG_FOLDER=$MOE_CONFIG_DIR

serve_args=()
append_tuning_args
export EXTRA_ARGS="${serve_args[*]} ${EXTRA_ARGS:-}"

echo "node=$(hostname -s) port=$PORT job=$SLURM_JOB_ID model=$SERVED_NAME" \
     "started=$(date -Is)" | tee "$HANDOFF"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader \
  | tee -a "$HANDOFF"
python3 -c 'import vllm, torch; print("vllm", vllm.__version__, "torch", torch.__version__)' \
  | tee -a "$HANDOFF"

# The server is restarted inside the allocation if it exits, so that a crash
# does not cost a new queue wait.
attempt=0
while [ "$attempt" -lt "${MAX_ATTEMPTS:-5}" ]; do
  attempt=$((attempt + 1))
  log=$JOBLOGS/vllm_aiprover_s3_${SLURM_JOB_ID}_a${attempt}.log
  echo "attempt=$attempt log=$log $(date -Is)" | tee -a "$HANDOFF"
  bash "$SCRIPTS_DIR/serve_aiprover.sh" "$MODEL_DIR" > "$log" 2>&1
  echo "vllm exited rc=$? $(date -Is)"
  sleep 30
done
