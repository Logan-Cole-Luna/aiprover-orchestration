#!/bin/bash
# AIProver model server on one GPU node.
#
#   serve.sbatch CHECKPOINT [PARTITION] NODES WALL_TIME
#
# Run directly, the script submits itself and prints "Submitted batch job N",
# the form of `cluster.submit` in configs/site.json. Run as a job, it serves
# the checkpoint with vLLM on all GPUs of the node and writes the handoff file
# $HANDOFF ("node=<n> port=<p> job=<id> model=<name>") before the weights load;
# clients poll /v1/models for readiness. AIPROVER_SERVER_ARGS adds sbatch
# options, such as the GPU type, memory, and CPUs.
#
# The virtualenv VLLM_VENV and the Python it uses are extracted to node-local
# disk from their packs (pack.sh) together with the checkpoint, so that nothing
# is read from the shared file system while serving.
#
# Environment: HANDOFF, VLLM_VENV, SERVED_NAME [aiprover], PORT [8060],
# MAX_MODEL_LEN [65536], GPU_UTIL [0.93], MAX_ATTEMPTS [5], EXTRA_ARGS. A
# checkpoint with params.json is served in Mistral format; any other checkpoint
# with the parsers TOOL_CALL_PARSER [hermes] and REASONING_PARSER [none].
#
#SBATCH --job-name=aiprover_server
#SBATCH --gpus-per-node=4
set -uo pipefail

if [ -z "${SLURM_JOB_ID:-}" ]; then
  script=$(readlink -f "$0") root=$(dirname "$script")/../..
  nodes=${@: -2:1} wall=${@: -1} partition=
  [ $# -ge 4 ] && partition=$2
  mkdir -p "$root/logs"
  exec sbatch ${partition:+--partition="$partition"} --nodes="$nodes" --time="$wall" \
    --output="$root/logs/%x_%j.out" --export=ALL,MODEL_DIR="$(readlink -f "$1")" \
    ${AIPROVER_SERVER_ARGS:-} "$script"
fi

fail() { echo "[serve] $*" >&2; exit 1; }
root=${AIPROVER_ORCHESTRATION_ROOT:-$SLURM_SUBMIT_DIR}
work=$SLURM_TMPDIR
venv=$(readlink -m "${VLLM_VENV:?set VLLM_VENV}")
gpus=$(nvidia-smi -L | wc -l)
name=${SERVED_NAME:-aiprover}
mkdir -p "$(dirname "$HANDOFF")"
echo "[serve] $(hostname) job $SLURM_JOB_ID: $MODEL_DIR on $gpus GPUs"

# Node-local virtualenv and Python, relocated to their new paths. Only the
# packs are read, so the original directories need not exist.
unpack() { bash "$root/scripts/slurm/pack.sh" -x "${AIPROVER_PACK_ROOT:-$(dirname "$1")}/$(basename "$1").pack" "$work"; }
unpack "$venv" || fail "cannot extract the pack of $venv; run pack.sh on it"
local_venv=$work/$(basename "$venv")
python_home=$(readlink -m "$(dirname "$(sed -n 's/^home *= *//p' "$local_venv/pyvenv.cfg")")")
unpack "$python_home" || fail "cannot extract the pack of $python_home; run pack.sh on it"
python=$work/$(basename "$python_home")
ln -sfn "$python/bin/$(basename "$(readlink "$local_venv/bin/python")")" "$local_venv/bin/python"
sed -i "s|^home = .*|home = $python/bin|" "$local_venv/pyvenv.cfg"
grep -lI "^#!$venv/bin/" "$local_venv"/bin/* 2>/dev/null \
  | xargs -r sed -i "1s|^#!$venv/bin/|#!$local_venv/bin/|"
site=$(echo "$local_venv"/lib/python*/site-packages)

# The model is text-only, and a torchcodec built for another CUDA version fails
# at import with an OSError, which stops vLLM; hide it.
[ -d "$site/torchcodec" ] && mv "$site/torchcodec" "$site/torchcodec.disabled"
# The Marlin kernels repack kv_b_proj into integer weights, and vLLM casts the
# chunked-context activations of multi-head latent attention to that dtype. Cast
# to the layer's activation dtype instead when the weight is not a float.
mla=$site/vllm/model_executor/layers/attention/mla_attention.py
if grep -q 'kv_c_normed = kv_c_normed.to(self.kv_b_proj.weight.dtype)$' "$mla" 2>/dev/null; then
  sed -i 's/kv_c_normed = kv_c_normed.to(self.kv_b_proj.weight.dtype)$/kv_c_normed = kv_c_normed.to(self.kv_b_proj.weight.dtype if self.kv_b_proj.weight.dtype.is_floating_point else self.kv_b_proj.params_dtype)/' "$mla"
fi
"$local_venv/bin/python" -c "import vllm.entrypoints.cli.main" || fail "vLLM does not import"

model=$work/model
mkdir -p "$model"
find -L "$MODEL_DIR" -maxdepth 1 -type f | xargs -P 8 -I{} cp -L {} "$model/" || fail "cannot copy $MODEL_DIR"
"$python/bin/python3" -I -c '
import glob, json, os, sys
index = glob.glob(sys.argv[1] + "/*.safetensors.index.json")
shards = set(json.load(open(index[0]))["weight_map"].values()) if index else set()
missing = [f for f in shards if not os.path.isfile(os.path.join(sys.argv[1], f))]
sys.exit(1 if missing or not shards else 0)' "$model" || fail "incomplete checkpoint $MODEL_DIR"

export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1
export VLLM_CACHE_ROOT=$work/cache/vllm TRITON_CACHE_DIR=$work/cache/triton
export TORCHINDUCTOR_CACHE_DIR=$work/cache/inductor
# FlashInfer's sampler compiles with nvcc on first use, which compute nodes lack.
export VLLM_USE_FLASHINFER_SAMPLER=0
if [ -f "$model/params.json" ]; then
  format=(--tokenizer-mode mistral --config-format mistral --load-format mistral
          --tool-call-parser mistral --reasoning-parser mistral)
else
  format=(--tool-call-parser "${TOOL_CALL_PARSER:-hermes}")
  [ -n "${REASONING_PARSER:-}" ] && format+=(--reasoning-parser "$REASONING_PARSER")
fi
echo "node=$(hostname -s) port=${PORT:-8060} job=$SLURM_JOB_ID model=$name started=$(date -Is)" | tee "$HANDOFF"
for attempt in $(seq "${MAX_ATTEMPTS:-5}"); do
  echo "[serve] attempt $attempt $(date -Is)"
  # shellcheck disable=SC2086  # EXTRA_ARGS is a list of options.
  "$local_venv/bin/vllm" serve "$model" --served-model-name "$name" \
    --host 0.0.0.0 --port "${PORT:-8060}" --tensor-parallel-size "$gpus" \
    --max-model-len "${MAX_MODEL_LEN:-65536}" \
    --gpu-memory-utilization "${GPU_UTIL:-0.93}" \
    --enable-auto-tool-choice "${format[@]}" ${EXTRA_ARGS:-}
  echo "[serve] vLLM exited with $? at $(date -Is)"
  sleep 30
done
