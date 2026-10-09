#!/bin/bash -l
#SBATCH -J aiprover_bench
#SBATCH -p gh-dev
#SBATCH -N 2
#SBATCH --ntasks-per-node=1
#SBATCH -c 64
#SBATCH -t 02:00:00
#SBATCH -A CDA25016
#
# Decode-speed comparison of server configurations of the AIProver model on
# the allocation of serve_aiprover_vista.sbatch (same container, Ray cluster
# and arguments, vllm_common.sh). Each arm restarts Ray and `vllm serve` with
# its settings, waits for /v1/models, runs the loads, and stops the server.
#
#   MODEL_DIR=/work/11428/pjana/aiprover_model sbatch scripts/bench_serve_vista.sbatch
#
# Arms (ARMS, run in order; [default "A B C D E F"]):
#   A  eager mode, untuned MoE kernels (configuration before this benchmark)
#   B  CUDA graphs and torch.compile, untuned MoE kernels
#   C  B with tuned MoE kernels (tune_moe_vista.sbatch; equals B without them)
#   D  C with n-gram speculative decoding (SPEC_TOKENS, default 4)
#   E  C with the FlashMLA attention backend
#   F  C with an FP8 KV cache (lossy: numerics differ)
#   G  C with GPU n-gram speculative decoding
#
# Loads per arm (vllm bench serve):
#   c1_8k     1 request at a time, 8,000 input / 800 output tokens
#   c1_80k    1 at a time, 76,000 shared-prefix + 4,000 input / 800 output
#   c13_80k   13 concurrent, as c1_80k (production: ~13 running, ~83k context)
#   edit_c8   8 concurrent chat requests that rewrite session Lean files
#             (make_edit_dataset.py; greedy; texts kept for comparison)
#
# Results: $SCRATCH/joblogs/bench_<job>/<arm>_<load>.json, server logs, and the
# spec-decode counters after the edit load (<arm>_metrics.txt).
# Environment: MODEL_DIR (required), ARMS, EDIT_DATASET, READY_TIMEOUT_SEC,
# SCRIPTS_DIR, and the settings of serve_aiprover_vista.sbatch.
set -uo pipefail

SCRIPTS_DIR=${SCRIPTS_DIR:-$WORK/aiprover_serve/scripts}
source "$SCRIPTS_DIR/vllm_common.sh"
ARMS=${ARMS:-A B C D E F}
EDIT_DATASET=${EDIT_DATASET:-$WORK/aiprover_serve/edit_dataset.jsonl}
READY_TIMEOUT_SEC=${READY_TIMEOUT_SEC:-1800}
OUT=$JOBLOGS/bench_$SLURM_JOB_ID
mkdir -p "$OUT"
BIND="$BIND --bind $OUT --bind $(dirname "$EDIT_DATASET")"
URL=http://127.0.0.1:$PORT

arm_settings() {
  case $1 in
    A) echo "ENFORCE_EAGER=1 MOE_TUNED=0 GPU_MEMORY_UTILIZATION=0.90" ;;
    B) echo "MOE_TUNED=0" ;;
    C) echo "" ;;
    D) echo "SPEC_METHOD=ngram" ;;
    E) echo "ATTENTION_BACKEND=FLASHMLA" ;;
    F) echo "KV_CACHE_DTYPE=fp8" ;;
    G) echo "SPEC_METHOD=ngram_gpu" ;;
    *) echo "unknown arm $1" >&2; return 1 ;;
  esac
}

# Returns 0 once the API answers, 1 if the server exits or the wait times out.
wait_ready() {
  local server_pid=$1 waited=0
  while [ "$waited" -lt "$READY_TIMEOUT_SEC" ]; do
    curl -sf "$URL/v1/models" >/dev/null && return 0
    kill -0 "$server_pid" 2>/dev/null || return 1
    sleep 10
    waited=$((waited + 10))
  done
  return 1
}

# bench <arm> <load> <vllm bench serve arguments>
bench() {
  local arm=$1 load=$2
  shift 2
  echo "  $load $(date -Is)"
  container vllm bench serve \
    --base-url "$URL" --model "$MODEL_DIR" --served-model-name "$SERVED_NAME" \
    --tokenizer-mode "$TOKENIZER_MODE" --seed 0 --disable-tqdm \
    --percentile-metrics ttft,tpot,itl,e2el --metric-percentiles 50,90,99 \
    --save-result --result-dir "$OUT" --result-filename "${arm}_$load.json" \
    "$@" > "$OUT/${arm}_$load.log" 2>&1 \
    || echo "  $load failed (see ${arm}_$load.log)"
}

random_load=(--backend vllm --dataset-name random --ignore-eos --random-output-len 800)
long_context=(--random-prefix-len 76000 --random-input-len 4000)

run_arm() {
  local arm=$1 settings
  settings=$(arm_settings "$arm") || return
  (
    [ -n "$settings" ] && export $settings
    [ "${MOE_TUNED:-1}" = 0 ] && unset VLLM_TUNED_CONFIG_FOLDER
    build_serve_args
    echo "arm $arm [$settings] $(date -Is)"
    serve_summary | tee "$OUT/$arm.settings"
    ray_cluster_down
    ray_cluster_up
    container env RAY_ADDRESS="$head_ip:$RAY_PORT" \
      vllm serve "${serve_args[@]}" > "$OUT/$arm.server.log" 2>&1 &
    server_pid=$!
    if wait_ready "$server_pid"; then
      echo "  ready $(date -Is)"
      bench "$arm" c1_8k "${random_load[@]}" --random-input-len 8000 \
        --num-prompts 4 --max-concurrency 1
      bench "$arm" c1_80k "${random_load[@]}" "${long_context[@]}" \
        --num-prompts 3 --max-concurrency 1
      bench "$arm" c13_80k "${random_load[@]}" "${long_context[@]}" \
        --num-prompts 39 --max-concurrency 13
      if [ -f "$EDIT_DATASET" ]; then
        bench "$arm" edit_c8 --backend openai-chat --endpoint /v1/chat/completions \
          --dataset-name custom --dataset-path "$EDIT_DATASET" \
          --custom-output-len -1 --num-prompts 16 --max-concurrency 8 \
          --disable-shuffle --temperature 0 --save-detailed \
          --extra-body '{"reasoning_effort": "high"}'
        curl -s "$URL/metrics" | grep -E "^vllm:spec_decode" > "$OUT/${arm}_metrics.txt"
      fi
    else
      echo "  server not ready (see $arm.server.log)"
    fi
    kill "$server_pid" 2>/dev/null
    wait "$server_pid" 2>/dev/null
    pkill -u "$USER" -f "vllm serve" 2>/dev/null
    ray_cluster_down
  )
}

for arm in $ARMS; do
  run_arm "$arm"
done
ls -l "$OUT"
