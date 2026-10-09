# Shared by serve_aiprover_vista.sbatch and bench_serve_vista.sbatch: the
# container environment, the Ray cluster across the allocation, and the
# `vllm serve` arguments. Sourced inside a Slurm allocation once MODEL_DIR is
# set; settings are read from the environment (see serve_aiprover_vista.sbatch).
#
# Defaults are applied once, at source time; build_serve_args reads the
# variables when called, so a caller may override them per launch.

set +u; module load tacc-apptainer/1.4.1; set -u

: "${MODEL_DIR:?set MODEL_DIR to the AIProver checkpoint directory}"
SERVED_NAME=${SERVED_NAME:-aiprover}
PORT=${PORT:-8060}
RAY_PORT=${RAY_PORT:-6379}
SIF=${SIF:-$WORK/containers/vllm-gh200.sif}
if [ -f "$MODEL_DIR/params.json" ]; then
  CKPT_FORMAT=mistral
  PARALLEL=${PARALLEL:-pp}
  MAX_MODEL_LEN=${MAX_MODEL_LEN:-1048576}
elif [ -f "$MODEL_DIR/config.json" ]; then
  CKPT_FORMAT=hf
  PARALLEL=${PARALLEL:-tp}
  MAX_MODEL_LEN=${MAX_MODEL_LEN:-262144}
else
  echo "ERROR: $MODEL_DIR has neither params.json (Mistral) nor config.json (HF)" >&2
  exit 1
fi
GPU_MEMORY_UTILIZATION=${GPU_MEMORY_UTILIZATION:-0.93}
WEIGHT_HBM_GIB=${WEIGHT_HBM_GIB:-70}
QUANTIZATION=${QUANTIZATION:-}
source "$(dirname "${BASH_SOURCE[0]}")/vllm_args.sh"
TOKENIZER_MODE=${TOKENIZER_MODE:-mistral}
EXTRA_ARGS=${EXTRA_ARGS:-}
JOBLOGS=$SCRATCH/joblogs
mkdir -p "$JOBLOGS"
[ -f "$SIF" ] || { echo "ERROR: container $SIF not found" >&2; exit 1; }

# Triton locates libcuda through Apptainer's bind mount, and compiles with
# the container's gcc rather than a host compiler on PATH.
export TRITON_LIBCUDA_PATH=/.singularity.d/libs
export CC=/usr/bin/gcc CXX=/usr/bin/g++
# Compute nodes have no egress; the checkpoint is read from disk only.
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export TMPDIR=/tmp/$USER
# GH200 HBM is counted in the node's memory, so Ray's memory monitor sees the
# vLLM KV-cache profile plus the checkpoint read as >95% use and kills a rank;
# the kernel's OOM handling is left in charge instead. vLLM does not use the
# Ray object store for weights, so it is kept small.
export RAY_memory_monitor_refresh_ms=${RAY_memory_monitor_refresh_ms:-0}
OBJECT_STORE_BYTES=${OBJECT_STORE_BYTES:-$((8 * 1024 ** 3))}
# Compile caches (torch.compile, CUDA graphs, Triton) are node-local: ranks
# sharing a Lustre cache directory fail with ESTALE
# (AIProver_plugin/serve/serve_vista_pp2.slurm).
export TRITON_CACHE_DIR=$TMPDIR/triton_$SLURM_JOB_ID VLLM_CACHE_ROOT=$TMPDIR/vllm_$SLURM_JOB_ID
mkdir -p "$TMPDIR"

BIND="--bind $MODEL_DIR"
# Tuned fused-MoE kernel configurations (tune_moe_vista.sbatch), named by
# expert shape, device and dtype; vLLM falls back to its defaults without one.
MOE_CONFIG_DIR=${MOE_CONFIG_DIR:-$WORK/aiprover_serve/moe_configs}
if [ -d "$MOE_CONFIG_DIR" ]; then
  export VLLM_TUNED_CONFIG_FOLDER=$MOE_CONFIG_DIR
  BIND="$BIND --bind $MOE_CONFIG_DIR"
fi

nodes=($(scontrol show hostnames "$SLURM_JOB_NODELIST"))
num_nodes=${#nodes[@]}
head_node=${nodes[0]}
head_ip=$(hostname -i | awk '{print $1}')

# Weights per rank and the offload needed to keep WEIGHT_HBM_GIB resident.
ckpt_gib=$(du -sb --apparent-size "$MODEL_DIR" | awk '{printf "%.1f", $1 / 2^30}')
if [ -z "${CPU_OFFLOAD_GB:-}" ]; then
  if [ -n "$QUANTIZATION" ] || [ "$CKPT_FORMAT" = mistral ]; then
    CPU_OFFLOAD_GB=0
  else
    CPU_OFFLOAD_GB=$(awk -v c="$ckpt_gib" -v n="$num_nodes" -v h="$WEIGHT_HBM_GIB" \
      'BEGIN { o = c / n - h; print (o > 0) ? int(o + 1) : 0 }')
  fi
fi

container() {
  apptainer exec --nv $BIND "$SIF" "$@"
}

# Ray state lives in the host /tmp, which persists between jobs and arms.
ray_cluster_down() {
  for node in "${nodes[@]}"; do
    srun --overlap --nodes=1 --ntasks=1 -w "$node" \
      apptainer exec --nv "$SIF" ray stop --force >/dev/null 2>&1 || true
  done
}

# One Ray cluster across the allocation; the head runs in the batch step (no
# srun) so that its task slot remains free.
ray_cluster_up() {
  container ray start --head --node-ip-address="$head_ip" --port="$RAY_PORT" \
    --num-gpus 1 --object-store-memory "$OBJECT_STORE_BYTES" --block &
  sleep 15
  local i
  for ((i = 1; i < num_nodes; i++)); do
    srun --overlap --nodes=1 --ntasks=1 -w "${nodes[$i]}" \
      apptainer exec --nv $BIND "$SIF" \
      ray start --address "$head_ip:$RAY_PORT" --num-gpus 1 \
      --object-store-memory "$OBJECT_STORE_BYTES" --block &
    sleep 5
  done
  sleep 20
}

# Sets the array serve_args from the current settings.
build_serve_args() {
  local parallel_args
  if [ "$PARALLEL" = pp ]; then
    parallel_args=(--tensor-parallel-size 1 --pipeline-parallel-size "$num_nodes")
  else
    parallel_args=(--tensor-parallel-size "$num_nodes")
  fi
  serve_args=(
    "$MODEL_DIR"
    --served-model-name "$SERVED_NAME"
    --host 0.0.0.0 --port "$PORT"
    "${parallel_args[@]}"
    --distributed-executor-backend ray
    --max-model-len "$MAX_MODEL_LEN"
    --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
    --tool-call-parser mistral --enable-auto-tool-choice
    --reasoning-parser mistral
    --tokenizer-mode "$TOKENIZER_MODE"
  )
  if [ "$CKPT_FORMAT" = mistral ]; then
    # FP8 experts: Triton kernels. vLLM's default choice, FlashInfer CUTLASS,
    # compiles its kernels at startup with one nvcc per core (~4 GB each),
    # which exhausts the head node's memory; vLLM's own CUTLASS FP8 MoE is
    # not available for this model's quantization scheme.
    serve_args+=(--config-format mistral --load-format mistral
                 --moe-backend "${MOE_BACKEND:-triton}")
  else
    # Mistral3 carries a Pixtral vision encoder; the harness sends text only.
    serve_args+=(--config-format hf --load-format safetensors
                 --limit-mm-per-prompt '{"image": 0}')
  fi
  [ "$CPU_OFFLOAD_GB" != "0" ] && serve_args+=(--cpu-offload-gb "$CPU_OFFLOAD_GB")
  [ -n "$QUANTIZATION" ] && serve_args+=(--quantization "$QUANTIZATION")
  append_tuning_args
  # Unquoted on purpose: EXTRA_ARGS holds several flags.
  serve_args+=($EXTRA_ARGS)
}

serve_summary() {
  echo "checkpoint=$MODEL_DIR size_gib=$ckpt_gib" \
       "per_rank_gib=$(awk -v c="$ckpt_gib" -v n="$num_nodes" 'BEGIN{printf "%.1f", c/n}')" \
       "format=$CKPT_FORMAT cpu_offload_gb=$CPU_OFFLOAD_GB" \
       "quantization=${QUANTIZATION:-none} eager=$ENFORCE_EAGER" \
       "spec=${SPEC_METHOD:-none} kv=$KV_CACHE_DTYPE" \
       "moe_configs=${VLLM_TUNED_CONFIG_FOLDER:-none}"
}
