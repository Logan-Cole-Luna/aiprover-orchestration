# Decode-speed settings of `vllm serve`, shared by the Vista and Stampede3
# server jobs (vllm_common.sh, serve_aiprover_s3.sbatch). Sourcing applies
# the defaults; append_tuning_args appends the flags to the array serve_args
# from the variables' values at call time. JSON values carry no spaces, so
# the flags also survive word splitting in an EXTRA_ARGS string.
#
#   ENFORCE_EAGER      1 disables CUDA graphs and torch.compile   [0]
#   CUDAGRAPH_MAX      largest CUDA-graph batch, in tokens        [128]
#   SPEC_METHOD        ngram | ngram_gpu speculative decoding     [off]
#   SPEC_TOKENS        speculative tokens per step                [4]
#   KV_CACHE_DTYPE     auto | fp8                                 [auto]
#   ATTENTION_BACKEND  vLLM attention backend                     [vLLM default]

ENFORCE_EAGER=${ENFORCE_EAGER:-0}
CUDAGRAPH_MAX=${CUDAGRAPH_MAX:-128}
SPEC_METHOD=${SPEC_METHOD:-}
SPEC_TOKENS=${SPEC_TOKENS:-4}
KV_CACHE_DTYPE=${KV_CACHE_DTYPE:-auto}

append_tuning_args() {
  serve_args+=(--kv-cache-dtype "$KV_CACHE_DTYPE")
  [ -n "${ATTENTION_BACKEND:-}" ] && serve_args+=(--attention-backend "$ATTENTION_BACKEND")
  # Decode is bound by kernel launches in eager mode (~6B active parameters
  # per token); CUDA graphs replay a step as one launch. Capture sizes are
  # capped above the largest decode batch (requests x speculative tokens).
  if [ "$ENFORCE_EAGER" = "1" ]; then
    serve_args+=(--enforce-eager)
  else
    serve_args+=(--compilation-config "{\"max_cudagraph_capture_size\":$CUDAGRAPH_MAX}")
  fi
  # Prompt-lookup speculation: drafts are copied from the context, which the
  # agent's edits and file writes repeat; verification keeps the output
  # distribution unchanged.
  if [ -n "$SPEC_METHOD" ]; then
    serve_args+=(--speculative-config "{\"method\":\"$SPEC_METHOD\",\"num_speculative_tokens\":$SPEC_TOKENS,\"prompt_lookup_min\":2,\"prompt_lookup_max\":5}")
  fi
  return 0
}
