#!/usr/bin/env bash
set -euo pipefail

# Everything is overridable from the environment so the same image can serve
# the 8B model on one card and the 32B across several.
MODEL_ID="${MODEL_ID:-Qwen/Qwen3-VL-8B-Instruct}"
SERVED_NAME="${SERVED_MODEL_NAME:-${MODEL_ID##*/}}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-16384}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"
TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-1}"
# Documents arrive as several page renders in one request.
LIMIT_MM_PER_PROMPT="${LIMIT_MM_PER_PROMPT:-image=6}"

echo "Serving ${MODEL_ID} as '${SERVED_NAME}'"
echo "  context: ${MAX_MODEL_LEN}, GPU utilisation: ${GPU_MEMORY_UTILIZATION}, TP: ${TENSOR_PARALLEL_SIZE}"

exec python3 -m vllm.entrypoints.openai.api_server \
    --model "${MODEL_ID}" \
    --served-model-name "${SERVED_NAME}" \
    --host 0.0.0.0 \
    --port 8000 \
    --max-model-len "${MAX_MODEL_LEN}" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
    --limit-mm-per-prompt "${LIMIT_MM_PER_PROMPT}" \
    --trust-remote-code \
    "$@"
