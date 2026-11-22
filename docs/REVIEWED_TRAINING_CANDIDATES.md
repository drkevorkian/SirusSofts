# Offline reviewed training candidates (source-only, not deployed)

## Scope and what this does not do

The optional \`sirus-ai-dual-tunnel/gentoo/reviewed_training_candidates.py\`
utility prepares **mechanics measurement Q&A candidates** from human-supported
claims in the *private* Gentoo continuous-research evidence ledger. It is not a
general training pipeline for all Can-Am, Polaris, Honda, Yamaha, programming,
science, mathematics, dictionary or thesaurus material. Those need separate
parsers, provenance rules, safety review and domain-specific evaluation.

**No model training, model-weight mutation, autonomous adapter promotion,
remote publication or CMS synchronization is implemented here.** The regular
continuous research loop continues to generate unreviewed drafts; the draft
outputs are never used as authoritative candidate answers.

The source-backed selection requires all of the following:

- A supported claim's **latest** version still says supported and was reviewed
  by a CMS administrator.
- The candidate belongs to a current, eligible, nonexcluded original PDF and
  retains exact page, part, quote SHA-256, source and section revisions.
- An operator-assigned nonempty make/model/year[/engine/assembly/revision]
  applicability is unchanged and agrees with the claim's recorded review.
- The separate, private PDF ingestion database still has an independently
  approved source with the identical update timestamp, and the **literal**
  original quotation still appears at the indexed PDF page and part.
- Conflicting numeric values for the same scope/property/unit, including
  contradictory *unreviewed* original measurements, exclude that property.
- Duplicate measurements for the same scope/property/unit are reduced to one
  conservative candidate; this is not independent source corroboration.

Even when all conditions pass, this only confirms a human reviewed the *literal
original quote*. It does not establish the repair procedure's safety, correctness
for every vehicle variant, copyright permission to train, or generalization by
the model. The prompts explicitly request a citation and forbid applying the
measurement to a different model. Reconcile inventory and review all candidates
before any separately approved model-training experiment.

## Read-only preview and explicitly authorized private export

Use the exact local private paths from the deployed configuration after a
reviewed in-place upgrade; the examples below are **placeholders, not a
deployment command**:

\`\`\`bash
python3 sirus-ai-dual-tunnel/gentoo/reviewed_training_candidates.py \
  --input-db /ABSOLUTE/PRIVATE/input.sqlite3 \
  --pdf-db /ABSOLUTE/PRIVATE/pdf_ingest.sqlite3
\`\`\`

Preview only: output is counts, with no dataset files or database writes. To
create candidates, an operator first provisions an existing owned private
directory (no group/other access), then explicitly supplies its absolute path:

\`\`\`bash
python3 sirus-ai-dual-tunnel/gentoo/reviewed_training_candidates.py \
  --input-db /ABSOLUTE/PRIVATE/input.sqlite3 \
  --pdf-db /ABSOLUTE/PRIVATE/pdf_ingest.sqlite3 \
  --output-dir /ABSOLUTE/PRIVATE/training-candidates
\`\`\`

The exporter refuses to overwrite files and writes new 0600 JSONL and a
checksummed manifest in the private directory. It refuses symlink directories
and public-permission directories. The manifests mark these as candidates only
and require independent approval plus original-source rechecks before training.

Train/evaluation candidates are separated by a deterministic hash of the
**original PDF source ID**, not by randomly splitting passages from the same
book. Small libraries can produce zero held-out examples and are **not
evaluation-ready**. The candidate JSONL format can be parsed by the existing
offline QLoRA dataset validator, but no autonomous integration or training is
enabled. The current QLoRA runner requires a separate compatible local CUDA
fine-tuning environment and explicit opt-in; the existing local GPU setup's
training capacity has not been established.

## Remaining Phase E work

1. Administrator review/curation of individual training examples, licensing,
   conflict adjudication, safety-critical procedure policy, and a durable
   approval/revocation mechanism.
2. Closed-book and problem-solving benchmark sets spanning the requested
   mechanics, code generation/debugging, science and mathematics skills.
   Include counterfactual/model-year tests, held-out *whole* manuals, and
   explicit abstention for unsupported questions.
3. Full-corpus duplicate and near-duplicate detection and validation that
   repeated conclusions are not counted as independent evidence.
4. GPU/CPU training-hardware admission, offline versioned LoRA/QLoRA snapshots,
   candidate-versus-baseline comparisons and automatic rejection/rollback on
   regression, **before** any approved continuous adaptation policy.
5. Full separate backup/recovery and live Ubuntu/Gentoo acceptance. No live
   servers have been updated or used to test this source-only increment.

Run the focused source tests with
\`python3 -m unittest discover -s sirus-ai-dual-tunnel/tests -p 'test_reviewed_training_candidates.py' -v\`.
