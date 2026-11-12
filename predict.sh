#!/usr/bin/env bash
# Entry point of the frozen solution: reads /data, writes /result/submission.jsonl and /result/logs/.
set -uo pipefail
cd "$(dirname "$0")"
# Our model fits one GPU; two would change nothing but the layer split.
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
MODEL=("${MODEL_PATH:-models/qwen3-14b}"/*.gguf)
# The trained slot-filling adapter, when present, fills the form; LORA_PATH= (empty) runs the base model alone.
LORA=("${LORA_PATH-models/lora}"/*.gguf)
EXTRA=()
if [ -n "${LORA_PATH-models/lora}" ] && [ -f "${LORA_PATH-models/lora}/slots-lora-3.gguf" ]; then
  EXTRA=(--lora "${LORA_PATH-models/lora}/slots-lora-3.gguf")
elif [ -f "${LORA[0]}" ]; then
  LAST_LORA="${LORA[${#LORA[@]}-1]}"
  EXTRA=(--lora "$LAST_LORA")
fi
# Optional second model, of another family, for the SQL fallback's prompt B. Its parameters count toward the 15B total,
# so it is only possible with a main model of 13B or less (README.md).
if [ -n "${VERIFIER_PATH:-}" ] && [ -f "${VERIFIER_PATH}" ]; then
  EXTRA+=(--verifier-model "${VERIFIER_PATH}")
fi
# DATAMATE_RULES=train keeps only the code rules the training split backs; all (the default) keeps every rule.
export DATAMATE_RULES="${DATAMATE_RULES:-all}"
if command -v conda > /dev/null && conda env list | grep -q '^datamate-agent '; then
  PY=(conda run --no-capture-output -n datamate-agent python)
elif command -v python3 > /dev/null; then
  PY=(python3)
else
  PY=(python)
fi
exec "${PY[@]}" -m scripts.run_metric_layer \
  --data "${DATA_DIR:-/data}" --output "${RESULT_DIR:-/result}" --question-set all \
  --model "${MODEL[0]}" --runtime "${RUNTIME_DIR:-.runtime-cuda}" --databases "${CACHE_DIR:-/tmp/datamate-databases}" "${EXTRA[@]}" "$@"
