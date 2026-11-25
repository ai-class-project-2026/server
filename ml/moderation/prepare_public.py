"""Download the public profanity datasets and convert them to our format.

    uv run python ml/moderation/prepare_public.py

Writes data/moderation/raw/<name>.jsonl (training) and
data/moderation/eval_only/<name>.jsonl (never trained on). Both are gitignored.
Sources and licenses are listed in data/README.md.

Labels follow plan 9.8: 1 only for actual profanity. Rows about hate or
implied offense without profanity are skipped, because they are neither a
clean example nor something this model should hide.
"""

import csv
import json
import urllib.request
from collections import defaultdict
from pathlib import Path

RAW = Path("data/moderation/raw")
EVAL = Path("data/moderation/eval_only")
SOURCES = RAW / "_sources"

CURSE = "https://raw.githubusercontent.com/2runo/Curse-detection-data/ff241621e103b6f220d30de324d0d07987887308"
BEEP = "https://raw.githubusercontent.com/kocohub/korean-hate-speech/f8d05dce2b22007bb149e5139c0060c68ad8f94b"
KMHAS = "https://raw.githubusercontent.com/adlnlp/K-MHaS/ec7a7e775d650b825872f6f538fc717822cdfc1a"
UNSMILE = "https://raw.githubusercontent.com/smilegate-ai/korean_unsmile_dataset/569ab5f495e046911898805d3bee92469988dcd9"
CHATBOT = "https://raw.githubusercontent.com/songys/Chatbot_data/4cf20d13fc46f5037fd1c531cd566e2dd9f72974"
KOTOX = "https://huggingface.co/datasets/ssgyejin/KOTOX/resolve/87907083ce28109467c72efad19792cd176ee1ca"

# KOTOX's toxic side is mostly implied offense, so keep only rows with a real swear.
EXPLICIT = ("시발", "씨발", "병신", "존나", "개새", "지랄", "새끼", "좆")


def fetch(name: str, base: str, file: str) -> Path:
    path = SOURCES / name / file.replace("/", "_")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(f"{base}/{file}", tmp)
        tmp.rename(path)
    return path


def tsv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))


def curse():
    for line in fetch("curse", CURSE, "dataset.txt").read_text("utf-8").splitlines():
        text, _, label = line.rpartition("|")
        if label in ("0", "1"):
            yield text, int(label)


def beep():
    for file in ("labeled/train.tsv", "labeled/dev.tsv"):
        for r in tsv(fetch("beep", BEEP, file)):
            if r["hate"] == "none":  # "offensive" and "hate" are not always profanity
                yield r["comments"], 0


def kmhas(*splits: str):
    for split in splits:
        lines = fetch("kmhas", KMHAS, f"data/kmhas_{split}.txt").read_text("utf-8")
        for line in lines.splitlines()[1:]:
            text, _, labels = line.rpartition("\t")
            labels = set(labels.split(","))
            if "3" in labels:  # Profanity
                yield text, 1
            elif labels == {"8"}:  # Not Hate Speech
                yield text, 0


def kotox(*splits: str):
    for split in splits:
        path = fetch("kotox", KOTOX, f"data/total/{split}.csv")
        with path.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if not any(w in r["neutral"].replace(" ", "") for w in EXPLICIT):
                    yield r["neutral"], 0
                    yield r["neutral_obfuscated_texts"], 0
                if any(w in r["toxic"].replace(" ", "") for w in EXPLICIT):
                    yield r["toxic"], 1
                    yield r["toxic_obfuscated_texts"], 1


def chatbot():  # everyday chat, which the comment datasets above lack
    swears = (*EXPLICIT, "ㅅㅂ", "ㅂㅅ", "ㅈㄴ", "미친", "꺼져", "썅")
    with fetch("chatbot", CHATBOT, "ChatbotData.csv").open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if not any(w in r["Q"].replace(" ", "") for w in swears):
                yield r["Q"], 0


def unsmile():  # CC BY-NC-ND: evaluation only, never augmented or trained on
    for file in ("unsmile_train_v1.0.tsv", "unsmile_valid_v1.0.tsv"):
        for r in tsv(fetch("unsmile", UNSMILE, file)):
            if r["악플/욕설"] == "1":
                yield r["문장"], 1
            elif r["clean"] == "1":
                yield r["문장"], 0


def write(sets: dict[str, list], folder: Path):
    """One line per sentence; a sentence labeled both ways is unusable, so drop it."""
    labels = defaultdict(set)
    sets = {n: [(" ".join(t.split()), y) for t, y in rows] for n, rows in sets.items()}
    for rows in sets.values():
        for text, label in rows:
            labels[text].add(label)
    folder.mkdir(parents=True, exist_ok=True)
    seen = set()
    for name, rows in sets.items():
        out = []
        for text, label in rows:
            if text and text not in seen and len(labels[text]) == 1:
                seen.add(text)
                out.append({"text": text, "label": label})
        (folder / f"{name}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), "utf-8"
        )
        bad = sum(r["label"] for r in out)
        print(f"{folder / name}.jsonl: {len(out)} (profane {bad}, clean {len(out) - bad})")


def main():
    write(
        {
            "curse": list(curse()),
            "kmhas": list(kmhas("train", "valid")),
            "beep_clean": list(beep()),
            "kotox": list(kotox("train", "valid")),
            "chatbot_clean": list(chatbot()),
        },
        RAW,
    )
    for name, rows in {
        "kmhas_test": kmhas("test"),
        "kotox_test": kotox("test"),
        "unsmile": unsmile(),
    }.items():
        write({name: list(rows)}, EVAL)


if __name__ == "__main__":
    main()
