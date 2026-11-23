"""QLoRA fine-tune of a small instruct model on the synthetic chats (plan 8.7).
Run on a GPU machine. The training packages are not server dependencies:

    git clone <server repo> && cd server
    uv sync  # the training code imports the app package
    uv pip install torch --index-url https://download.pytorch.org/whl/cu128
    uv pip install "transformers>=4.46" "trl>=0.12" peft bitsandbytes datasets accelerate
    uv run python ml/schedule/train_lora.py --data data/schedule/synth/train.jsonl --out lora-v1

Then serve: merge the adapter, convert to GGUF (llama.cpp convert_hf_to_gguf.py,
q4_K_M), run `llama-server -m model.gguf --port 8080`, and set
SCHEDULE_LLM_URL=http://localhost:8080 SCHEDULE_MODEL_VERSION=lora-v1 on the server.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path[:0] = ["backend", "ml/common"]
os.environ.setdefault("JWT_SECRET", "training-only-placeholder-secret-000")
from app.schedule.prompt import build_messages
from app.schemas import ScheduleRequest


def to_chat(record: dict) -> dict:
    """Same prompt as serving (app.schedule.prompt) + the gold answer as the reply."""
    answer = json.dumps({"events": record["events"]}, ensure_ascii=False)
    return {
        "messages": build_messages(ScheduleRequest.model_validate(record))
        + [{"role": "assistant", "content": answer}]
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="Qwen/Qwen2.5-3B-Instruct")
    ap.add_argument("--epochs", type=float, default=2)
    args = ap.parse_args()

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    rows = [
        to_chat(json.loads(line))
        for line in Path(args.data).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    split = Dataset.from_list(rows).train_test_split(test_size=0.05, seed=0)

    tok = AutoTokenizer.from_pretrained(args.base)
    model = AutoModelForCausalLM.from_pretrained(
        args.base,
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        ),
        device_map="auto",
    )
    trainer = SFTTrainer(
        model=model,
        processing_class=tok,
        train_dataset=split["train"],
        eval_dataset=split["test"],
        peft_config=LoraConfig(
            r=16,
            lora_alpha=32,
            lora_dropout=0.05,
            target_modules="all-linear",
            task_type="CAUSAL_LM",
        ),
        args=SFTConfig(
            output_dir=args.out,
            num_train_epochs=args.epochs,
            per_device_train_batch_size=2,
            gradient_accumulation_steps=8,
            learning_rate=2e-4,
            max_length=2048,
            assistant_only_loss=True,  # learn the JSON answer, not the prompt
            eval_strategy="epoch",
            logging_steps=10,
            bf16=True,  # fp16's grad scaler can't handle bf16 LoRA weights
        ),
    )
    trainer.train()
    trainer.save_model(args.out)


if __name__ == "__main__":
    main()
