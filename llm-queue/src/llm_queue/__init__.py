"""llm-queue — B2 front-ended inference admission controller.

Sits between LiteLLM (the caller-facing front door) and the llama.cpp backend
(llama-swap → llama-server). It *holds and dispatches* requests instead of
letting llama-swap drop the overflow with a flat ``429 Too many requests``:

  callers → llama-cpp:8080 (alias) → llm-gateway (LiteLLM) → llm-queue → *-upstream

Design: documentation/implementation-guide/LiteLLM-Proxy/DESIGN-B2-inference-queue.md
"""

__version__ = "0.1.0"
