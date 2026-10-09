#!/bin/bash -l
#SBATCH -J aiprover_srv
#SBATCH -p gh
#SBATCH -N 2
#SBATCH --ntasks-per-node=1
#SBATCH -c 64
#SBATCH -t 48:00:00
#SBATCH -A CDA25016
#SBATCH -o aiprover_srv_%j.out
#SBATCH -e aiprover_srv_%j.err
#
# AIProver model server on TACC Vista (GH200, one GPU per node).
#
# One Ray cluster spans the allocated nodes; `vllm serve` runs on the head
# node with the model split across them. The OpenAI-compatible API listens on
# the head node at $PORT. Clients locate the
# server through $HANDOFF (`node=<n> port=<p> job=<id> model=<name>`), written
# before the weights load; readiness is established by polling /v1/models
# (TACC.md §10). The orchestration VM reaches the port through an SSH forward
# on the vista.sock ControlMaster (configs/aiprover/vista.toml).
#
# Submit from a Vista login node (scripts/submit_aiprover_vista.sh):
#   MODEL_DIR=/work/.../aiprover_ckpt sbatch scripts/serve_aiprover_vista.sbatch
#
# Checkpoint formats (detected from MODEL_DIR):
#   Mistral native  params.json + tekken.json + consolidated-*.safetensors,
#                   e.g. the trained AIProver model (FP8, ~112 GB). Loaded with
#                   the flags of AIProver/AIProver_plugin/serve/: Mistral config
#                   and loader, pipeline parallelism across the nodes (PARALLEL=pp;
#                   PP passes activations between nodes only, TP all-reduces every
#                   layer), full 1,048,576-token context. Fits in HBM on 2 nodes.
#   HF              config.json + safetensors (per-expert layout,
#                   scripts/unpack_experts_vista.sbatch); tensor parallelism.
#
# Memory (HF bf16). HBM is 95 GiB per node and is carved out of the node's 212 GiB
# (TACC.md §7). A bf16 Leanstral-class checkpoint (~222 GiB) needs ~111 GiB
# per rank at TP=2, which exceeds HBM. Two remedies are configurable:
#   CPU_OFFLOAD_GB  GiB of weights per rank held in Grace memory and read over
#                   NVLink-C2C (default: computed from the checkpoint size so
#                   that WEIGHT_HBM_GIB of weights stay resident per GPU).
#   QUANTIZATION    e.g. "fp8": online weight quantization (~56 GiB per rank,
#                   no offload, numerics differ from the measured bf16 model).
# Four nodes (TP=4; TP must divide the 32 heads) serve bf16 without offload.
#
# Environment (all optional except MODEL_DIR):
#   MODEL_DIR           checkpoint directory, Mistral or HF format (required)
#   PARALLEL            pp or tp across nodes             [pp Mistral, tp HF]
#   SERVED_NAME         served model id                   [aiprover]
#   PORT                API port on the head node         [8060]
#   HANDOFF             handoff file                      [$SCRATCH/servers/aiprover_server.txt]
#   SIF                 aarch64 vLLM image                [$WORK/containers/vllm-gh200.sif]
#   MAX_MODEL_LEN       context length                    [1048576 Mistral, 262144 HF]
#   GPU_MEMORY_UTILIZATION                                [0.93]
#   WEIGHT_HBM_GIB      resident weights per GPU (auto offload target) [70]
#   ATTENTION_BACKEND   vLLM attention backend            [vLLM default]
#   TOKENIZER_MODE      mistral (tekken.json, no tokenizer.json) [mistral]
#   MOE_BACKEND         FP8 expert kernels (Mistral format) [triton]
#   MOE_CONFIG_DIR      tuned fused-MoE configs           [$WORK/aiprover_serve/moe_configs]
#   ENFORCE_EAGER       1 disables CUDA graphs and torch.compile [0]
#   CUDAGRAPH_MAX       largest CUDA-graph batch (tokens) [128]
#   SPEC_METHOD         ngram | ngram_gpu speculative decoding [off]
#   SPEC_TOKENS         speculative tokens per step       [4]
#   OBJECT_STORE_BYTES  Ray object store per node         [8 GiB]
#   RAY_memory_monitor_refresh_ms  0 disables Ray's OOM killer [0]
#   CPU_OFFLOAD_GB, QUANTIZATION, KV_CACHE_DTYPE, EXTRA_ARGS
#   SCRIPTS_DIR         directory of vllm_common.sh       [$WORK/aiprover_serve/scripts]
set -uo pipefail

HANDOFF=${HANDOFF:-$SCRATCH/servers/aiprover_server.txt}
SCRIPTS_DIR=${SCRIPTS_DIR:-$WORK/aiprover_serve/scripts}
source "$SCRIPTS_DIR/vllm_common.sh"
mkdir -p "$(dirname "$HANDOFF")"

echo "node=$head_node port=$PORT job=$SLURM_JOB_ID model=$SERVED_NAME" \
     "nodes=$num_nodes $PARALLEL=$num_nodes started=$(date -Is)" | tee "$HANDOFF"
serve_summary

ray_cluster_down
container python3 -c \
  'import vllm, torch; print("vllm", vllm.__version__, "torch", torch.__version__)' \
  | tee -a "$HANDOFF"
ray_cluster_up
build_serve_args

# The server is restarted inside the allocation if it exits, so that a crash
# does not cost a new queue wait (TACC.md §1, §8).
attempt=0
while [ "$attempt" -lt "${MAX_ATTEMPTS:-5}" ]; do
  attempt=$((attempt + 1))
  log=$JOBLOGS/vllm_aiprover_${SLURM_JOB_ID}_a${attempt}.log
  echo "attempt=$attempt log=$log $(date -Is)" | tee -a "$HANDOFF"
  container env RAY_ADDRESS="$head_ip:$RAY_PORT" \
    vllm serve "${serve_args[@]}" > "$log" 2>&1
  echo "vllm exited rc=$? $(date -Is)"
  sleep 30
done
