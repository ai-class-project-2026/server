"""Final schedule training set: every v2 chat + the useful, repaired part of v1.

    uv run python ml/schedule/build_train.py

v2 comes from ml/schedule/synth/generate.py. v1 was generated earlier and has known
flaws, so only chats that add something are kept: unfinished proposals, chats with
no plan, and confirmed plans that don't end by announcing "확정".
"""

import argparse
import collections
import copy
import json
import os
import random
import re
import sys
import datetime as dt
from pathlib import Path

sys.path[:0] = ["backend", "ml/common"]
os.environ.setdefault("JWT_SECRET", "training-only-placeholder-secret-000")
from app.schedule.prompt import build_messages
from app.schemas import ScheduleRequest
from dateparse_ko import parse

SYNTH = Path("data/schedule/synth")
V1_SHARE = 0.3  # at most this many v1 chats per v2 chat
_24H = re.compile(r"(?:(?P<hint>오후|저녁|밤)\s*)?(?<!\d)(?P<hour>1[3-9]|2[01])시(?!간)")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def natural_time(text: str) -> str:
    """19시 → 7시, 20시 → 저녁 8시 (plan 8.5: bare 8~11시 is ambiguous)."""

    def spoken(m: re.Match) -> str:
        hour = int(m["hour"]) - 12
        hint = m["hint"] or ("저녁" if hour >= 8 else "")
        return f"{hint} {hour}시".strip()

    return _24H.sub(spoken, text)


def useful_v1(r: dict) -> bool:
    if r["type"] in ("proposal_only", "not_schedule"):
        return True
    return r["type"] in ("confirm", "partial_absent") and "확정" not in r["dialogue"][-1]["text"]


def repair_v1(r: dict, rng: random.Random) -> dict:
    r = copy.deepcopy(r) | {"source": "v1"}
    if rng.random() < 0.75:  # keep some 24-hour times, people do write them
        for turn in r["dialogue"]:
            turn["text"] = natural_time(turn["text"])
    said = "".join(t["text"] for t in r["dialogue"]).replace(" ", "")
    known = {e["location"] for e in r["existing_events"]}
    for e in r["events"]:  # v1 sometimes answered with a place nobody mentioned
        place = e["location"]
        if place and place not in known and place.replace(" ", "") not in said:
            if not (place == "디스코드" and "디코" in said):
                e["location"] = None
    return r


def valid(r: dict) -> bool:
    try:
        build_messages(ScheduleRequest.model_validate(r))
    except ValueError:
        return False
    if {t["speaker"] for t in r["dialogue"]} - set(r["participants"]):
        return False
    ref = dt.date.fromisoformat(r["ref_datetime"][:10])
    found = {m.date.isoformat() for t in r["dialogue"] for m in parse(t["text"], ref) if m.date}
    return all(e["date"] in found for e in r["events"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", type=Path, default=SYNTH / "train_v1.jsonl")
    ap.add_argument("--v2", type=Path, default=SYNTH / "train_v2.jsonl")
    ap.add_argument("--out", type=Path, default=SYNTH / "train.jsonl")
    args = ap.parse_args()

    rng = random.Random(0)
    v2 = [r | {"source": "v2"} for r in read_jsonl(args.v2)]
    v1 = [repair_v1(r, rng) for r in read_jsonl(args.v1) if useful_v1(r)]
    rng.shuffle(v1)
    v1 = v1[: int(len(v2) * V1_SHARE)]
    seen, out, dropped = set(), [], collections.Counter()
    for r in v2 + v1:
        chat = tuple(re.sub(r"\s", "", t["text"]) for t in r["dialogue"])
        if chat in seen:
            dropped["duplicate"] += 1
        elif not valid(r):
            dropped[f"invalid {r['source']}"] += 1
        else:
            seen.add(chat)
            out.append(r)
    rng.shuffle(out)
    args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8")
    print(f"{args.out}: {len(out)} chats", dict(collections.Counter(r["source"] for r in out)), "dropped", dict(dropped))
    print("types", dict(collections.Counter(r["type"] for r in out).most_common()))
    print("statuses", dict(collections.Counter(e["status"] for r in out for e in r["events"]).most_common()))


def demo():
    assert natural_time("19시 반 ㄱ") == "7시 반 ㄱ"
    assert natural_time("오후 15시") == "오후 3시"
    assert natural_time("20시 줌") == "저녁 8시 줌"
    assert natural_time("밤 21시") == "밤 9시"
    assert natural_time("10시간 12시") == "10시간 12시"
    ev = {"title": "회의", "date": "2026-10-16", "start": "19:00", "location": "카페", "status": "proposed",
          "attendees": [], "absent": []}  # fmt: skip
    turn = {"speaker": "민준", "text": "금 19시 ㄱ?", "ts": "2026-10-14T21:00:00+09:00"}
    r = {"type": "proposal_only", "ref_datetime": turn["ts"], "participants": ["민준"], "dialogue": [turn],
         "existing_events": [], "events": [ev]}  # fmt: skip
    fixed = repair_v1(r, random.Random(0))
    assert fixed["events"][0]["location"] is None and r["events"][0]["location"] == "카페"
    assert repair_v1(r | {"existing_events": [ev]}, random.Random(0))["events"][0]["location"] == "카페"
    assert valid(fixed) and not valid(fixed | {"participants": ["서연"]})
    assert useful_v1(r) and not useful_v1(r | {"type": "revise"})
    done = {"speaker": "민준", "text": "금 7시 확정!", "ts": turn["ts"]}
    assert not useful_v1(r | {"type": "confirm", "dialogue": [done]})
    print("ok")


if __name__ == "__main__":
    demo() if sys.argv[1:] == ["--demo"] else main()
