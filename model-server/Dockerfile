# vLLM serving Qwen3-VL, exposed on an OpenAI-compatible endpoint.
#
# The application never depends on this image specifically: it speaks
# /v1/chat/completions, so any compatible server can take its place.
FROM vllm/vllm-openai:latest

COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 8000

# Weight loading takes minutes on a cold start, so the probe is patient.
HEALTHCHECK --interval=30s --timeout=10s --start-period=600s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/start.sh"]
