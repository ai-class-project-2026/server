"""Public data + augmentation → data/moderation/train.jsonl (part 3 → part 1).

    uv run python ml/moderation/build_train.py

1. Convert each public dataset into data/moderation/raw/<name>.jsonl, one
   {"text": "...", "label": 0 | 1} per line (raw/ is gitignored, plan 16.6).
   Chat data adds "context": ["earlier message", ...], oldest first; the label
   is about text only. Write the source and license in data/README.md.
   UnSmile is eval-only.
2. Run this script: every sentence is kept as-is and augmented with the rules
   in ml/moderation/augment, test sets C/D are removed, the rest is shuffled.
3. Send the printed counts to part 1 with the PR.
"""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from augment import augment

RAW = Path("data/moderation/raw")
TESTS = [Path("data/moderation/test_C_human"), Path("data/moderation/test_D_fp")]
OUT = Path("data/moderation/train.jsonl")
N_AUG = 3  # augmented copies per sentence


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def main():
    rng = random.Random(0)
    test_texts = {r["text"] for d in TESTS for f in d.glob("*.jsonl") for r in read_jsonl(f)}
    raw = [r for f in sorted(RAW.glob("*.jsonl")) for r in read_jsonl(f)]
    # A sentence and its variants share a group, so train.py keeps them on one
    # side of the validation split.
    groups = {r["text"]: i for i, r in enumerate(raw)}
    rows = {}
    for r in raw:
        context = r.get("context", [])
        if test_texts.intersection(context):  # test sentences can't be context either
            continue
        for text in [r["text"], *augment(r["text"], rng, N_AUG)]:
            if text not in test_texts:  # never train on the test sets
                # Same words in another context can have another label.
                rows[text, tuple(context)] = {
                    "label": r["label"],
                    "group": groups.get(text, groups[r["text"]]),
                }
    out = [{"text": t, "context": list(c), **v} for (t, c), v in rows.items()]
    rng.shuffle(out)
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out))
    bad = sum(r["label"] for r in out)
    n_groups = len({r["group"] for r in out})
    print(
        f"{OUT}: {len(out)} rows from {n_groups} sentences "
        f"(profane {bad}, clean {len(out) - bad})"
    )


if __name__ == "__main__":
    main()
