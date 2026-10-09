#!/bin/bash -l
#SBATCH -J aiprover_moe_tune
#SBATCH -p gh-dev
#SBATCH -N 4
#SBATCH --ntasks-per-node=1
#SBATCH -c 64
#SBATCH -t 02:00:00
#SBATCH -A CDA25016
#
# Tunes the fused-MoE Triton kernels of the AIProver model on GH200 and
# writes E=128,N=2048,device_name=NVIDIA_GH200_120GB,dtype=fp8_w8a8.json to
# $MOE_CONFIG_DIR, where vllm_common.sh points VLLM_TUNED_CONFIG_FOLDER.
# Without that file vLLM uses its untuned default configuration.
#
# The tuner times 1,920 kernel configurations per batch size (about 12 min
# each on one GH200) and spreads the batch sizes over the GPUs of a Ray
# cluster, here one GPU per node of the allocation.
#
# The tuner reads expert shapes from an HF config; the Mistral3 wrapper of
# the base checkpoint is not among its architectures, so its text_config
# (DeepseekV3, same experts as the trained model) is written out as a
# standalone config. The trained model quantizes FP8 per tensor, so no block
# shape applies.
#
#   sbatch scripts/tune_moe_vista.sbatch
#
# Environment: HF_CONFIG_DIR [$SCRATCH/aiprover_ckpt/leanstral_base_unpacked],
# MOE_CONFIG_DIR [$WORK/aiprover_serve/moe_configs], BATCH_SIZES, SCRIPTS_DIR.
set -uo pipefail

HF_CONFIG_DIR=${HF_CONFIG_DIR:-$SCRATCH/aiprover_ckpt/leanstral_base_unpacked}
export MOE_CONFIG_DIR=${MOE_CONFIG_DIR:-$WORK/aiprover_serve/moe_configs}
SCRIPTS_DIR=${SCRIPTS_DIR:-$WORK/aiprover_serve/scripts}
# Tokens per MoE call: decode batches (requests x speculative tokens) and
# prefill chunks; vLLM uses the nearest tuned size.
BATCH_SIZES=${BATCH_SIZES:-1 2 4 8 16 24 32 48 64 96 128 256 512 1024 2048 4096}
mkdir -p "$MOE_CONFIG_DIR"

MODEL_DIR=$HF_CONFIG_DIR source "$SCRIPTS_DIR/vllm_common.sh"
text_config=$SCRATCH/moe_tune_$SLURM_JOB_ID
mkdir -p "$text_config"
BIND="$BIND --bind $text_config"
python3 - "$HF_CONFIG_DIR/config.json" "$text_config/config.json" <<'EOF'
import json, sys
text = json.load(open(sys.argv[1]))["text_config"]
text["architectures"] = ["DeepseekV3ForCausalLM"]
text.pop("quantization_config", None)
json.dump(text, open(sys.argv[2], "w"), indent=1)
EOF

ray_cluster_down
ray_cluster_up
container env RAY_ADDRESS="$head_ip:$RAY_PORT" \
  python3 /opt/vllm/benchmarks/kernels/benchmark_moe.py \
  --model "$text_config" --tp-size 1 --dtype fp8_w8a8 --tune \
  --batch-size $BATCH_SIZES --save-dir "$MOE_CONFIG_DIR"
ray_cluster_down
rm -rf "$text_config"
ls -l "$MOE_CONFIG_DIR"
