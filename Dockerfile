# BASE_IMAGE must be the one the organizers' Docker guideline prescribes; python:3.12-slim is only a stand-in until then.
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}
WORKDIR /solution
# llama-server links against OpenMP, which slim images leave out.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
# The large files come first, so a code change does not copy 10 GB of weights again.
COPY .runtime-cuda ./.runtime-cuda
# The GGUF weights sit here at build time; they are not in git.
COPY models/qwen3-14b ./models/qwen3-14b
# The slot-filling LoRA adapter (scripts/train_slots.py), applied to the form calls only.
COPY models/lora ./models/lora
COPY pyproject.toml LICENSE predict.sh requirements-train.txt ./
COPY README.md ./
COPY datamate ./datamate
COPY scripts ./scripts
RUN pip install --no-cache-dir -e .
ENTRYPOINT ["bash", "/solution/predict.sh"]
