"""Score schedule predictions against gold (plan 8.8).

    python eval/schedule/score.py data/schedule/real_test/test.jsonl preds.jsonl --label v1

gold line: a record with "events" (and optional "type"); pred line: {"events": [...]}.
Prints the metrics and appends a row to eval/reports/schedule.csv (dashboard input).
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def _key(e: dict) -> tuple[str, str]:
    return e["date"], (e.get("start") or "")[:5]


def _keys(events: list[dict], status: str) -> set:
    return {_key(e) for e in events if e["status"] == status}


def _f1(tp, n_pred, n_gold):
    p = tp / n_pred if n_pred else 1.0
    r = tp / n_gold if n_gold else 1.0
    return 2 * p * r / (p + r) if p + r else 0.0


def score(golds: list[dict], preds: list[dict]) -> dict:
    assert len(golds) == len(preds), "one prediction line per gold line"
    tot = defaultdict(int)
    by_type = defaultdict(lambda: defaultdict(int))
    for g, p in zip(golds, preds):
        gc, pc = _keys(g["events"], "confirmed"), _keys(p["events"], "confirmed")
        gx, px = _keys(g["events"], "cancelled"), _keys(p["events"], "cancelled")
        gold_dates = {e["date"] for e in g["events"]}
        for t in (tot, by_type[g.get("type", "all")]):
            t["tp"] += len(gc & pc)
            t["pred"] += len(pc)
            t["gold"] += len(gc)
        tot["cancel_gold"] += len(gx)
        tot["cancel_hit"] += len(gx & px)
        tot["events_pred"] += len(p["events"])
        tot["date_ok"] += sum(e["date"] in gold_dates for e in p["events"])
    return {
        "n": len(golds),
        "confirmed_f1": round(_f1(tot["tp"], tot["pred"], tot["gold"]), 4),
        # plan 8.8: share of "confirmed" outputs that weren't confirmed (most important)
        "false_registration": round((tot["pred"] - tot["tp"]) / tot["pred"], 4)
        if tot["pred"]
        else 0.0,
        "cancel_recall": round(tot["cancel_hit"] / tot["cancel_gold"], 4)
        if tot["cancel_gold"]
        else None,
        "date_accuracy": round(tot["date_ok"] / tot["events_pred"], 4)
        if tot["events_pred"]
        else None,
        "f1_by_type": {
            k: round(_f1(v["tp"], v["pred"], v["gold"]), 4)
            for k, v in sorted(by_type.items())
        },
    }


def _read(path):
    return [
        json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gold")
    ap.add_argument("pred")
    ap.add_argument(
        "--label",
        required=True,
        help="model/version name for the report, e.g. qwen3b-lora-v1",
    )
    ap.add_argument("--report", default="eval/reports/schedule.csv")
    args = ap.parse_args()

    m = score(_read(args.gold), _read(args.pred))
    print(json.dumps(m, ensure_ascii=False, indent=2))
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    new = not report.exists()
    with report.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(
                [
                    "label",
                    "testset",
                    "n",
                    "confirmed_f1",
                    "false_registration",
                    "cancel_recall",
                    "date_accuracy",
                ]
            )
        w.writerow(
            [
                args.label,
                Path(args.gold).name,
                m["n"],
                m["confirmed_f1"],
                m["false_registration"],
                m["cancel_recall"],
                m["date_accuracy"],
            ]
        )


if __name__ == "__main__":
    main()
