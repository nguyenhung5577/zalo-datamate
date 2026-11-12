# DataMate: offline Vietnamese business-data QA

Solution for the DataMate track of Zalo AI Challenge 2026. It answers Vietnamese questions from small-business owners over their raw CSV, Excel and SQLite files, fully offline, under a 15B-parameter budget. The metric is penalized accuracy (right +1, wrong −1, abstain 0), so every stage either verifies its answer or abstains. Questions pass through four stages in order:

1. **Code rules** (`datamate/direct.py`, `common.py`, `books.py`, `ledgers.py`) read the question and compute the answer over normalized and raw SQLite tables. No model is used.
2. **Model slots**: for what the rules decline, the base model first restates the question in plain written Vietnamese (`datamate/rewrite.py`; dropped if it changes a number, period, month, group or metric word), the rules try again on it, and then the trained Rank-16 LoRA adapter fills a structured slot form (metric, period, filters). Fixed SQL in `datamate/metrics.py` computes the answer after `check_evidence` rejects anything the question does not support.
3. **Dual-verified read-only SQL fallback** (`datamate/sql_fallback.py`): for unseen schemas or ad-hoc questions outside both the rules and the slot form, the base model (`lora=False`) synthesizes two independent read-only SQLite queries (`Prompt A` at `temperature=0`, `Prompt B` at `temperature=0.2, seed=2027`) enforced by a SQLite authorizer (`SELECT`/`READ`/`FUNCTION` only) and 3 s timeout, emitting an answer only when both queries succeed and produce identical scored results.
4. **Abstain** when no stage is verified.

`datamate/rulebook_check.py` makes questions abstain when a rulebook line they depend on has lost a term the code relies on.

**Rule scope.** `DATAMATE_RULES=train` (or `--rules-scope train`) keeps only the rules that took part in answering at least 5 training questions. The list in `datamate/rule_provenance.json` is built from the training split alone by `scripts/rule_census.py`. A question that only a switched-off rule recognises abstains. `all`, the default, keeps every rule.

## Models used at inference

| Model | File | Parameters | License | Released |
|---|---|---|---|---|
| Qwen3-14B | `Qwen3-14B-Q5_K_M.gguf` from Qwen/Qwen3-14B-GGUF, sha256 `e7c9aba1129ca2936be9eca01419d9f86af40e08caa01230d5574b34d08e3e31` | 14,768,307,200 | Apache-2.0 | April 2025 |
| Our LoRA adapter for Qwen3-14B | `models/lora/slots-lora-3.gguf` | 64,225,280 | Trained on `train/` only (see Training) | November 2026 |

Together: 14,832,532,480 parameters, under the 15B limit. `--verifier-model` / `VERIFIER_PATH` can add a second model for the SQL fallback's prompt B. It is not used in this configuration: with Qwen3-14B there is no room left under 15B. Any second model must be declared here with its parameter count. There is no other model (both the rewrite step and the dual-verified SQL fallback reuse the already-loaded Qwen3-14B base model with `lora=False`, adding 0 parameters). Both run on llama.cpp for CUDA 12.8+, bundled in `.runtime-cuda`.

Weights are not in git: the adapter's sha256 is in `models/lora/slots-lora-3.gguf.sha256`.

## Training

The rules were written from the training set, and from the rulebooks and question wording of the released splits. No test answers were used.

The adapter is trained on the training split only, for the form-filling step. The code still computes every answer.

1. `python -m scripts.build_labels` writes `results/training/slots-dev.jsonl` from the odd training shops.
   - A label is the slots that pass `check_evidence` and give the reference answer of `train/answers.jsonl`.
   - Candidates are the model's own earlier replies and slots built by code.
   - A question whose words no form fits is labelled `other`.
2. `python -m scripts.train_slots --model <Qwen/Qwen3-14B weights> --output results/training/lora-2` trains the adapter.
   - Packages: `requirements-train.txt`, on one 80 GB GPU, about 45 minutes on an H100.
   - Settings: rank 16 on every linear layer, 2 epochs, lr 1e-4, loss on the reply only.
3. `python convert_lora_to_gguf.py --outtype f16 --base <Qwen3-14B config> --outfile models/lora/slots-lora-2.gguf results/training/lora-2` converts it. The script is from llama.cpp b11895.

`scripts/fair_test.py` checked it on the 15 even training shops, which were not used for labels.

## Build and run

The GGUF must be at `models/qwen3-14b/` and the adapter at `models/lora/` before building. `BASE_IMAGE` is `python:3.12-slim` unless the organizers' guideline prescribes another.

```bash
docker build -t datamate .
```

```bash
docker run --rm --gpus all --network none -v /path/to/test:/data:ro -v /path/to/result:/result datamate
```

`predict.sh` reads `/data` and writes `/result/submission.jsonl`, one log per question in `/result/logs/` (question id, final answer, elapsed time, model reply) and `/result/model-server.log`. It uses GPU 0 only. If the adapter cannot load, the run retries with the base model alone; if the model cannot start, it continues by code rules. Either way it says so in its output and in `run.json`.

1000 public questions took 312 s on one NVIDIA Blackwell GPU, including model loading.

## Development

Use the `datamate-agent` Conda environment (`environment.yml`). The datasets, weights and runtime stay out of git.

| Path | What it is |
|---|---|
| `datamate/` | Data loading, normalizer, code rules, fixed metrics, model server, scoring |
| `scripts/run_metric_layer.py` | The runner `predict.sh` calls |
| `scripts/build_labels.py`, `scripts/train_slots.py` | Training labels and the LoRA training for the form-filling step |
| `scripts/check_direct.py` | Scores the code rules on train: odd shops are read to write rules, even shops stay unread |
| `scripts/fair_test.py` | Fair test on the even train shops, as asked and in chat shorthand; scores runner output by the stage that answered |
| `scripts/replay_metric_public.py` | Rebuilds a public file from a run's saved model replies, without the model |
| `scripts/stress_normalizer.py` | Rewrites a shop's layout and checks that the normalizer holds |
| `colab/train_slots_any.ipynb` | LoRA training and fair-set evaluation on Colab (any base model, resumable) |
| `results/public/` | Uploaded public files and model replies, not in git |
| `docs/HISTORY.md` | Public score history |

```bash
python -m unittest discover -s tests -q
```
