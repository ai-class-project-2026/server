"""Score the profanity pipeline on a labeled set (plan 9: test sets A-D).

    uv run python eval/moderation/score.py data/moderation/test_C_human/test.jsonl --label dict-v0

Lines: {"text": "...", "label": 0 | 1, "type": "숫자 치환" (optional)}.
Runs app.moderation.moderate() in-process, so it measures exactly what chat uses.
Appends to eval/reports/moderation.csv.
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path[:0] = ["backend", "ml/common"]
os.environ.setdefault("JWT_SECRET", "eval-only-placeholder-secret-0000000")
from app.moderation import moderate


def metrics(labels: list[int], preds: list[int]) -> dict:
    tp = sum(y and p for y, p in zip(labels, preds))
    fp = sum(p and not y for y, p in zip(labels, preds))
    fn = sum(y and not p for y, p in zip(labels, preds))
    neg = sum(not y for y in labels)
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 1.0
    return {
        "f1": round(f1, 4),
        "false_positive_rate": round(fp / neg, 4) if neg else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("testset")
    ap.add_argument("--label", required=True)
    ap.add_argument("--level", type=int, default=2)
    ap.add_argument("--report", default="eval/reports/moderation.csv")
    args = ap.parse_args()

    rows = [
        json.loads(x) for x in Path(args.testset).read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    t = time.perf_counter()
    preds = [int(moderate(r["text"], level=args.level).is_profane) for r in rows]
    ms = (time.perf_counter() - t) * 1000 / max(len(rows), 1)
    labels = [r["label"] for r in rows]
    m = metrics(labels, preds) | {"n": len(rows), "ms_per_msg": round(ms, 2)}
    by_type = defaultdict(lambda: ([], []))
    for r, p in zip(rows, preds):
        by_type[r.get("type", "all")][0].append(r["label"])
        by_type[r.get("type", "all")][1].append(p)
    m["f1_by_type"] = {k: metrics(*v)["f1"] for k, v in sorted(by_type.items())}
    misses = [r["text"] for r, p in zip(rows, preds) if r["label"] != p]
    print(json.dumps(m, ensure_ascii=False, indent=2))
    print("틀린 예시:", misses[:20])

    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    new = not report.exists()
    with report.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(
                ["label", "testset", "n", "f1", "false_positive_rate", "ms_per_msg"]
            )
        w.writerow(
            [
                args.label,
                Path(args.testset).parent.name,
                m["n"],
                m["f1"],
                m["false_positive_rate"],
                m["ms_per_msg"],
            ]
        )


if __name__ == "__main__":
    main()
