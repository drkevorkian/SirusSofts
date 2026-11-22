#!/usr/bin/env python3
"""Explicit offline QLoRA training runner for verified CMS JSONL exports.

--check imports no ML packages, writes nothing, and makes no network calls.
--run requires a local Hugging Face checkpoint and compatible CUDA hardware.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

PROFILES = ("all", "coding", "science", "mathematics", "physics", "time", "relativity", "finance", "mechanics")
MAX_DATASET_BYTES = 128 * 1024 * 1024
MAX_ROWS = 100000

def load_examples(filename, profile="all"):
    path = Path(filename)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("Dataset must be a regular absolute local file, not a symlink")
    if path.stat().st_size > MAX_DATASET_BYTES:
        raise ValueError("Dataset exceeds 128 MiB limit")
    examples = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        if len(examples) >= MAX_ROWS:
            raise ValueError("Too many examples")
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSONL line " + str(lineno)) from exc
        if not isinstance(data, dict):
            raise ValueError("Invalid example type at line " + str(lineno))
        # The reviewed-PDF exporter emits *candidates* for a separate human
        # training approval step. They must never flow straight into --run.
        # Approved CMS JSONL exports do not carry this source-only marker.
        if "candidate_only" in data:
            raise ValueError("Unapproved research candidate is not a training example at line " + str(lineno))
        domain = data.get("profile", "all")
        if domain not in PROFILES:
            raise ValueError("Unknown domain in dataset at line " + str(lineno))
        if profile != "all" and domain != profile:
            continue
        messages = data.get("messages")
        if not isinstance(messages, list) or len(messages) != 2:
            raise ValueError("Expected a user/assistant pair at line " + str(lineno))
        user, assistant = messages
        if not isinstance(user, dict) or not isinstance(assistant, dict) \
                or user.get("role") != "user" or assistant.get("role") != "assistant" \
                or not isinstance(user.get("content"), str) \
                or not isinstance(assistant.get("content"), str) \
                or not 0 < len(user["content"]) <= 12000 \
                or not 0 < len(assistant["content"]) <= 30000:
            raise ValueError("Invalid conversational example at line " + str(lineno))
        examples.append({"prompt": [user], "completion": [assistant]})
    return examples

def verify_split(train, evaluation):
    prompts = {hashlib.sha256(e["prompt"][0]["content"].encode()).digest() for e in train}
    for row in evaluation:
        if hashlib.sha256(row["prompt"][0]["content"].encode()).digest() in prompts:
            raise ValueError("Training and held-out evaluation share a prompt")

def local_checkpoint(directory):
    path = Path(directory)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir() \
            or not (path/"config.json").is_file():
        raise ValueError("Use an absolute local Hugging Face model directory with config.json")
    if list(path.glob("*.gguf")):
        raise ValueError("GGUF inference artifacts are not valid QLoRA source checkpoints")
    return path

def train(args, examples, evaluation):
    # These guards execute before importing ML libraries, allocating GPU RAM or writing files.
    model_dir = local_checkpoint(args.model_dir)
    output = Path(args.output_dir)
    if not output.is_absolute() or output.exists() or output.is_symlink():
        raise ValueError("New output directory must be an absolute path that does not exist")
    if len(examples) < 10:
        raise ValueError("Training needs at least 10 reviewed training examples")
    if args.r < 4 or args.r > 128 or args.max_length < 256 or args.max_length > 4096 \
            or args.epochs <= 0 or args.epochs > 10 or args.learning_rate <= 0 \
            or args.learning_rate > 0.0005:
        raise ValueError("Training hyperparameters outside allowed range")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    from datasets import Dataset
    import torch
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer
    if torch.version.cuda is None or not torch.cuda.is_available():
        raise RuntimeError("A compatible NVIDIA CUDA training environment is required")
    available = torch.cuda.get_device_properties(0).total_memory
    if available < 12 * 1024 ** 3 and not args.allow_low_vram:
        raise RuntimeError("Less than 12 GiB CUDA VRAM; use a suitable training GPU, or explicitly override the guard")
    quant = BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type="nf4",
                               bnb_4bit_use_double_quant=True,
                               bnb_4bit_compute_dtype=torch.float16)
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir),local_files_only=True,
                                               trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(
        str(model_dir),local_files_only=True,trust_remote_code=False,
        quantization_config=quant,dtype=torch.float16,device_map={"":0})
    model.config.use_cache = False
    adapter = LoraConfig(r=args.r,lora_alpha=args.r*2,lora_dropout=0.05,
                         target_modules="all-linear",task_type="CAUSAL_LM",bias="none")
    config = SFTConfig(
        output_dir=str(output),num_train_epochs=args.epochs,
        per_device_train_batch_size=1,per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.grad_accum,learning_rate=args.learning_rate,
        max_length=args.max_length,seed=args.seed,fp16=True,bf16=False,
        gradient_checkpointing=True,logging_steps=1,save_strategy="epoch",
        eval_strategy="epoch" if evaluation else "no",report_to="none",
        completion_only_loss=True,assistant_only_loss=False,
        optim="paged_adamw_8bit")
    trainer = SFTTrainer(model=model,args=config,peft_config=adapter,
                         train_dataset=Dataset.from_list(examples),
                         eval_dataset=Dataset.from_list(evaluation) if evaluation else None,
                         processing_class=tokenizer)
    trainer.train()
    trainer.save_model(str(output))
    tokenizer.save_pretrained(str(output))
    print("Saved separate PEFT adapter to",output)
    print("Base model was not overwritten; GGUF conversion and staging are separate.")

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-jsonl",required=True)
    ap.add_argument("--eval-jsonl")
    ap.add_argument("--profile",choices=PROFILES,default="all")
    ap.add_argument("--model-dir")
    ap.add_argument("--output-dir")
    ap.add_argument("--run",action="store_true",help="Explicit opt-in to GPU training and output creation")
    ap.add_argument("--epochs",type=float,default=1.0)
    ap.add_argument("--r",type=int,default=16)
    ap.add_argument("--max-length",type=int,default=1024)
    ap.add_argument("--grad-accum",type=int,default=8)
    ap.add_argument("--learning-rate",type=float,default=0.0002)
    ap.add_argument("--seed",type=int,default=42)
    ap.add_argument("--allow-low-vram",action="store_true")
    args=ap.parse_args(argv)
    try:
        training=load_examples(args.train_jsonl,args.profile)
        evaluation=load_examples(args.eval_jsonl,args.profile) if args.eval_jsonl else []
        verify_split(training,evaluation)
        if args.model_dir:
            local_checkpoint(args.model_dir)
        print(json.dumps({"profile":args.profile,"train":len(training),"eval":len(evaluation),
                          "train_sha256":hashlib.sha256(Path(args.train_jsonl).read_bytes()).hexdigest(),
                          "mode":"run" if args.run else "check"},sort_keys=True))
        if args.run:
            if not args.model_dir or not args.output_dir:
                raise ValueError("--run requires --model-dir and --output-dir")
            train(args,training,evaluation)
        else:
            print("Validation only: no model loading, training, downloads, or writes.")
    except (ValueError, OSError, RuntimeError) as exc:
        print("ERROR: "+str(exc),file=sys.stderr)
        return 1
    return 0

if __name__=="__main__":
    sys.exit(main())
