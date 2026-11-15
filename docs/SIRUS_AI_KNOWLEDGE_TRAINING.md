# SirusSofts AI knowledge and training

## Delivered source

The private CMS admin route /admin/ai saves three specialty profiles
(coding, physics, time), review-gated reference documents, and verified
supervised question/answer examples in the existing private SQLite store.
Training, evaluation and knowledge are downloaded as separate JSONL exports.
References and examples are not written to GitHub or to the public web root.

The Gentoo AI input router optionally reads a locally transferred approved
knowledge JSONL file and retrieves relevant references using an offline
term-frequency relevance algorithm. Matching excerpts are inserted as bounded
(approximately 4,500-character), source-cited untrusted data. It does NOT train
LLM weights. No automatically network-fetched data, external APIs, or new
process-launching admin endpoint has been introduced.

## Operator deployment: not performed by GitHub commits

1. Review the SirusSofts commits and GitHub Actions tests.
2. Use the existing Hestia CMS upgrade --plan procedure and separately
   approved --apply procedure. Do not run legacy Plesk or Nginx installers.
3. Run the existing CMS/bin/migrate-schema.php as the restricted sirusweb
   account on the deployed CMS. This is an idempotent private schema upgrade.
4. Visit https://sirussofts.com/admin/ai as a CMS administrator. Save
   source-attributed references or supervised examples. Approve only material
   that has been fact-checked and licensed for your use.
5. Export Knowledge for RAG, LoRA Training or Held-out Evaluation JSONL.
   Never train on the evaluation export.
6. Transfer a reviewed knowledge export over a trusted administrative channel
   to Gentoo, for example under a private sirusai-owned directory.
   Restrict permissions to the AI account (0600). Do NOT put it under a web
   document root, post private content in public GitHub, or expose it in logs.
7. Validate the private file locally with:
      python3 sirus-ai-dual-tunnel/gentoo/knowledge.py --check /absolute/file.jsonl
   You can also test retrieval with --query "Lorentz factor" --profile physics.
8. When intentionally upgrading the Gentoo input worker, preserve the old
   service configuration and database and stage the updated knowledge.py and
   input_server.py together. Add --knowledge-file /absolute/file.jsonl or set
   SIRUS_AI_KNOWLEDGE_FILE to turn on retrieval. The existing one-time
   gentoo/install.sh refuses to overwrite live installations; do not rerun
   --install against an existing system. Roll back both files together.
   The new profile column in the input SQLite queue is added automatically
   and idempotently on input-worker startup. Existing queued requests default
   to the auto profile.
9. Deploy the revised AI web frontend separately behind the existing
   authenticated HTTPS gateway after normal operational review.

Without a configured knowledge file the model responds exactly as before;
selecting a topic only filters local references if retrieval is enabled.
The knowledge module validates review flags, record SHA-256, JSONL schema,
local absolute paths, symlinks, file size and reference count. It hot-reloads
valid changes between incoming requests. Imported reference instructions
are marked as untrusted; data poisoning still requires human review.

## Qwen specialist LoRA preparation

Export training and evaluation datasets as standard conversational JSONL
with user and assistant messages. Keep the evaluation set separate. Train
a compatible Qwen2.5-Coder-7B-Instruct adapter using original Hugging Face
model weights and a verified Transformers/PEFT/TRL training environment,
preferably on a supported GPU with sufficient VRAM. A Q4_K_M GGUF file is an
inference artifact, not the normal QLoRA training checkpoint. Your RX 590
Vulkan inference benchmark does not prove ROCm/PyTorch training support.

Record base model revision, tokenizer, licenses, dataset revision, learning
rate, LoRA rank, seed, training hardware and held-out metrics. Validate
regression benchmarks and scientific problem sets before using the fork's
convert_lora_to_gguf.py and staging a converted adapter. Do not merge an
unevaluated adapter into the currently served model. The PHP CMS never runs
training commands and the Gentoo worker never changes weights.

## Validation

    php CMS/tests/ai_training_test.php
    python3 -m unittest discover -s sirus-ai-dual-tunnel/tests -p "test_*.py" -v
    bash sirus-ai-dual-tunnel/gentoo/install.sh --check

This work introduces no background job, remote model download, auto-training,
GPU driver installation, system restart or live model change.
