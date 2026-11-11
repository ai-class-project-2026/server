"""Fine-tune KcELECTRA for profanity (plan 9.2 ③). Run on Colab:

    !git clone <server repo> && cd server
    !pip install -q "transformers>=4.46" datasets accelerate
    !python ml/moderation/train.py --train data/moderation/train.jsonl --out kcelectra-v1

Input jsonl lines: {"text": "...", "label": 0 | 1}. The model reads the
original text and the normalized text as a sentence pair (same as serving).
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path[:0] = ["backend"]
os.environ.setdefault("JWT_SECRET", "training-only-placeholder-secret-000")
from app.moderation.normalize import normalize


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="beomi/KcELECTRA-base-v2022")
    ap.add_argument("--epochs", type=float, default=3)
    args = ap.parse_args()

    from datasets import Dataset
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        Trainer,
        TrainingArguments,
    )

    rows = [
        json.loads(x) for x in Path(args.train).read_text().splitlines() if x.strip()
    ]
    for r in rows:
        r["norm"] = normalize(r["text"])[0]
    tok = AutoTokenizer.from_pretrained(args.base)
    ds = Dataset.from_list(rows).map(
        lambda b: tok(b["text"], b["norm"], truncation=True, max_length=128),
        batched=True,
    )
    split = ds.train_test_split(test_size=0.1, seed=0)
    model = AutoModelForSequenceClassification.from_pretrained(args.base, num_labels=2)
    Trainer(
        model=model,
        processing_class=tok,
        train_dataset=split["train"],
        eval_dataset=split["test"],
        args=TrainingArguments(
            output_dir=args.out,
            num_train_epochs=args.epochs,
            per_device_train_batch_size=32,
            learning_rate=3e-5,
            eval_strategy="epoch",
            save_strategy="epoch",
            load_best_model_at_end=True,
            fp16=True,
        ),
    ).train()
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)


if __name__ == "__main__":
    main()
