"""Fine-tune KcELECTRA for profanity (plan 9.2 ③). Run on a GPU machine:

    git clone <server repo> && cd server
    uv sync  # the training code imports the app package
    uv pip install torch --index-url https://download.pytorch.org/whl/cu128
    uv pip install "transformers>=4.46" datasets accelerate
    uv run python ml/moderation/train.py --train data/moderation/train.jsonl --out kcelectra-v1

Input jsonl lines: {"text": "...", "context": ["earlier message", ...],
"label": 0 | 1, "group": <source sentence id>}. context is oldest first and may
be empty; label is about text only. The model reads the original text, then the
normalized text plus context, as a sentence pair (same as serving).
"""

import argparse
import json
import os
import random
import sys
from pathlib import Path

sys.path[:0] = ["backend"]
os.environ.setdefault("JWT_SECRET", "training-only-placeholder-secret-000")
from app.moderation.classifier import second_segment
from app.moderation.normalize import normalize


def split_by_group(rows: list[dict], val_frac: float = 0.1, seed: int = 0, val_groups=None):
    """Keep every variant of a source sentence on one side, so val scores are honest.
    val_groups: the group ids that form the validation side (default: a seeded random 10%)."""
    key = lambda r: r.get("group", r["text"])
    if val_groups is None:
        groups = sorted({key(r) for r in rows}, key=str)
        random.Random(seed).shuffle(groups)
        val_groups = set(groups[: max(1, int(len(groups) * val_frac))])
    train, val = [], []
    for r in rows:
        (val if key(r) in val_groups else train).append(r)
    return train, val


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="beomi/KcELECTRA-base-v2022")
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--context-boost", type=int, default=4, help="how many times rows with context appear in training")
    ap.add_argument("--seed", type=int, help="Trainer seed and model init (unset: Trainer seed 42, init not seeded)")
    ap.add_argument(
        "--val-groups",
        type=Path,
        help="file with one group id per line: these groups are the validation side "
        "(unset: a seeded random 10%% of the groups). Keeps validation fixed when the data changes",
    )
    args = ap.parse_args()

    from datasets import Dataset
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    rows = [
        json.loads(x) for x in Path(args.train).read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    if any("group" not in r for r in rows):
        print(
            "WARNING: rows without 'group'; variants of one sentence can leak into val",
            file=sys.stderr,
        )
    for r in rows:
        r["has_context"] = bool(r.get("context"))
        r["norm"] = second_segment(normalize(r["text"])[0], r.pop("context", []))
    tok = AutoTokenizer.from_pretrained(args.base)

    def encode(batch_rows):
        return Dataset.from_list(batch_rows).map(
            lambda b: tok(b["text"], b["norm"], truncation=True, max_length=128),
            batched=True,
        )

    val_groups = None
    if args.val_groups:
        val_groups = {json.loads(x) for x in args.val_groups.read_text(encoding="utf-8").split()}
        missing = val_groups - {r.get("group", r["text"]) for r in rows}
        if missing:
            print(f"WARNING: {len(missing)} --val-groups ids are not in the data", file=sys.stderr)
    train_rows, val_rows = split_by_group(rows, val_groups=val_groups)
    if val_groups is not None:
        print(f"--val-groups: train rows {len(train_rows)}, val rows {len(val_rows)}")
    # Chats with context are a small share of the data, so show them more often.
    train_rows += [r for r in train_rows if r["has_context"]] * (args.context_boost - 1)
    if args.seed is not None:
        set_seed(args.seed)  # the classifier head is made here, before Trainer seeds anything
    model = AutoModelForSequenceClassification.from_pretrained(args.base, num_labels=2)
    Trainer(
        model=model,
        processing_class=tok,
        train_dataset=encode(train_rows),
        eval_dataset=encode(val_rows),
        args=TrainingArguments(
            output_dir=args.out,
            num_train_epochs=args.epochs,
            per_device_train_batch_size=32,
            learning_rate=3e-5,
            eval_strategy="epoch",
            save_strategy="epoch",
            load_best_model_at_end=True,
            fp16=True,
            seed=42 if args.seed is None else args.seed,
        ),
    ).train()
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)


if __name__ == "__main__":
    main()
