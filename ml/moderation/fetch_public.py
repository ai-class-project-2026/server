"""Download public profanity datasets (plan 9.4) and convert to {"text", "label"} jsonl.

    uv run python ml/moderation/fetch_public.py          # 2runo + BEEP + UnSmile
    uv run python ml/moderation/fetch_public.py --kold   # + KOLD (only once its license is confirmed)

Outputs (all gitignored, rerun to regenerate):
    data/moderation/raw/curse_2runo.jsonl     train + augment
    data/moderation/raw/beep.jsonl            train + augment
    data/moderation/raw/kold.jsonl            train (opt-in)
    data/moderation/test_A_public/unsmile.jsonl   EVAL ONLY (CC BY-NC-ND: no augmenting,
                                                  never put it under raw/)
Each line keeps the source label in "orig" so the mapping can be revisited later.
"""

import argparse
import csv
import io
import json
import urllib.request
from collections import Counter
from pathlib import Path

GH = "https://raw.githubusercontent.com"
RAW = Path("data/moderation/raw")
TEST_A = Path("data/moderation/test_A_public")


def get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8-sig")


def tsv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE))


def curse_2runo():
    """'문장|0 or 1' per line, 1 = 욕설."""
    for line in get(f"{GH}/2runo/Curse-detection-data/master/dataset.txt").splitlines():
        text, sep, label = line.rpartition("|")
        if sep and label.strip() in ("0", "1") and text.strip():
            yield {"text": text.strip(), "label": int(label), "orig": label.strip()}


def beep():
    """hate ∈ {hate, offensive, none}. hate/offensive → 1. Note: this is hate speech,
    not only 욕설, so "orig" is kept to filter later if it causes false positives."""
    for split in ("train", "dev"):
        for r in tsv(get(f"{GH}/kocohub/korean-hate-speech/master/labeled/{split}.tsv")):
            text = (r["comments"] or "").strip()
            if text:
                yield {"text": text, "label": int(r["hate"] != "none"), "orig": r["hate"]}


def kold():
    """OFF = offensive (True/False). Stored in git LFS."""
    url = "https://media.githubusercontent.com/media/boychaboy/KOLD/main/data/kold_v1.json"
    for r in json.loads(get(url)):
        text = (r["comment"] or "").strip()
        if text:
            yield {"text": text, "label": int(r["OFF"]), "orig": str(r["OFF"])}


def unsmile():
    """Multi-label. 악플/욕설 → 1, clean → 0. Rows that are hate but not 욕설 (e.g. only
    '지역') are dropped: they're neither clean nor profanity, so they'd blur the score."""
    base = f"{GH}/smilegate-ai/korean_unsmile_dataset/main"
    for split in ("train", "valid"):
        for r in tsv(get(f"{base}/unsmile_{split}_v1.0.tsv")):
            text = (r["문장"] or "").strip()
            if not text:
                continue
            if r["악플/욕설"] == "1":
                yield {"text": text, "label": 1, "orig": "악플/욕설"}
            elif r["clean"] == "1":
                yield {"text": text, "label": 0, "orig": "clean"}


def write(path: Path, rows) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    c = Counter(r["label"] for r in rows)
    print(f"{path}: {len(rows)}건 (욕설 {c[1]} / 정상 {c[0]})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kold", action="store_true", help="KOLD has no license file yet")
    args = ap.parse_args()

    write(RAW / "curse_2runo.jsonl", curse_2runo())
    write(RAW / "beep.jsonl", beep())
    if args.kold:
        write(RAW / "kold.jsonl", kold())
    write(TEST_A / "unsmile.jsonl", unsmile())


if __name__ == "__main__":
    main()
