# Model server

vLLM serving `Qwen3-VL-8B-Instruct` behind an OpenAI-compatible API.

The application only ever calls `/v1/chat/completions`, so this container is
replaceable. Anything that speaks that endpoint works: a hosted provider,
another local runtime, or a larger Qwen.

## Running

    docker compose --profile gpu up model-server

Needs an NVIDIA GPU with the container toolkit installed. The 8B model wants
roughly 20 GB of VRAM at bf16; weights are cached in a named volume, so only
the first start pays the download.

## Switching models

    MODEL_ID=Qwen/Qwen3-VL-32B-Instruct TENSOR_PARALLEL_SIZE=2 \
      docker compose --profile gpu up model-server

Then set `LLM_MODEL` on the backend and worker to the same served name. No
application code changes.

## Without a GPU

Point the backend at any compatible endpoint:

    LLM_BASE_URL=https://your-endpoint/v1 LLM_MODEL=your-model docker compose up

Or run the deterministic pipeline alone with `LLM_USE_MOCK=true`, which stubs
extraction and reasoning while leaving parsing, rules, comparison, reports and
QA fully exercised.
