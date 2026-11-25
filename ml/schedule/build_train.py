"""Final schedule training set: every v2 chat + the useful, repaired part of v1.

    uv run python ml/schedule/build_train.py

v2 comes from ml/schedule/synth/generate.py. v1 was generated earlier and has known
flaws, so only chats that add something are kept: unfinished proposals, chats with
no plan, and confirmed plans that don't end by announcing "확정".

Later sets go in after v2 and v3, in the order given, each tagged with its own source:

    uv run python ml/schedule/build_train.py --v3 data/schedule/synth/train_v3.jsonl \
        --extra v4=data/schedule/synth/train_v4.jsonl
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
from app.schedule.prompt import build_messages, to_kst
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


def held_out_texts() -> set[str]:
    """시험셋의 문장(공백 제거). 합성 대화에 우연히 같은 문장이 있어도 학습에서 빼요."""

    def strings(v):
        if isinstance(v, str):
            yield v
        elif isinstance(v, (list, dict)):
            for x in v.values() if isinstance(v, dict) else v:
                yield from strings(x)

    texts = set()
    for folder in (Path("data/schedule/real_test"), Path("data/schedule/pilot")):
        for path in folder.rglob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    texts |= {c for s in strings(json.loads(line)) if len(c := re.sub(r"\s", "", s)) >= 6}
                except ValueError:
                    continue
    return texts


def extra_file(arg: str) -> tuple[str, Path]:
    """"v4=data/x.jsonl" → ("v4", path); a bare path is named after its file ("x")."""
    name, sep, path = arg.partition("=")
    return (name, Path(path)) if sep else (Path(arg).stem, Path(arg))


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
        req = ScheduleRequest.model_validate(r)
        build_messages(req)
    except ValueError:
        return False
    if {t["speaker"] for t in r["dialogue"]} - set(r["participants"]):
        return False
    # 서빙(prompt.py)과 같은 규칙: 말한 시각(ts, 없으면 ref_datetime)을 한국 시각으로 바꾼 날짜 기준
    ref = to_kst(dt.datetime.fromisoformat(r["ref_datetime"]))
    found = {
        m.date.isoformat()
        for t in r["dialogue"]
        for m in parse(t["text"], to_kst(dt.datetime.fromisoformat(t["ts"]) if t.get("ts") else ref).date())
        if m.date
    }
    # 서빙 때 모델은 기존 일정(existing_events)도 보므로, 그 날짜도 "근거가 있는 날짜"다
    found |= {e.date.isoformat() for e in req.existing_events}
    return all(e["date"] in found for e in r["events"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", type=Path, default=SYNTH / "train_v1.jsonl")
    ap.add_argument("--v2", type=Path, default=SYNTH / "train_v2.jsonl")
    ap.add_argument("--v3", type=Path, help="short chats made with generate.py --short")
    ap.add_argument("--extra", action="append", default=[], metavar="[NAME=]FILE",
                    help="more chats (e.g. offline.py output), repeatable; source = NAME or the file name")  # fmt: skip
    ap.add_argument("--out", type=Path, default=SYNTH / "train.jsonl")
    args = ap.parse_args()

    rng = random.Random(0)
    v2 = [r | {"source": "v2"} for r in read_jsonl(args.v2)]
    v3 = [r | {"source": "v3"} for r in read_jsonl(args.v3)] if args.v3 else []
    extra = [r | {"source": name} for name, path in map(extra_file, args.extra) for r in read_jsonl(path)]
    v1 = [repair_v1(r, rng) for r in read_jsonl(args.v1) if useful_v1(r)]
    rng.shuffle(v1)
    v1 = v1[: int(len(v2) * V1_SHARE)]
    held_out = held_out_texts()
    seen, out, dropped = set(), [], collections.Counter()
    for r in v2 + v3 + extra + v1:
        chat = tuple(re.sub(r"\s", "", t["text"]) for t in r["dialogue"])
        if chat in seen:
            dropped["duplicate"] += 1
        elif held_out.intersection(chat):
            dropped["held-out"] += 1
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
    assert extra_file("v4=a/b.jsonl") == ("v4", Path("a/b.jsonl")) and extra_file("a/v5.jsonl") == ("v5", Path("a/v5.jsonl"))
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
    # 자정을 넘기는 대화: 23:59의 "내일"은 다음 날(1/1)이어야 하고, ref를 UTC로 적어도 결과가 같다
    mid = {"type": "confirm", "participants": ["민준", "서연"], "existing_events": [],
           "events": [ev | {"date": "2027-01-01", "status": "confirmed", "attendees": ["민준", "서연"]}],
           "dialogue": [{"speaker": "민준", "text": "내일 7시에 보자", "ts": "2026-12-31T23:59:00+09:00"},
                        {"speaker": "서연", "text": "좋아", "ts": "2027-01-01T00:03:00+09:00"}]}  # fmt: skip
    for ref in ("2027-01-01T00:05:00+09:00", "2026-12-31T15:05:00Z"):
        assert valid(mid | {"ref_datetime": ref})
    assert not valid(mid | {"ref_datetime": ref, "events": [ev | {"date": "2027-01-03"}]})
    # 옛 일정의 날짜가 대화에는 없고 기존 일정에만 있어도(수정 대화) 통과한다. 어디에도 없는 날짜는 탈락
    old = {"title": "회의", "date": "2026-11-09", "start": "19:00", "location": None, "status": "confirmed",
           "attendees": [], "absent": []}  # fmt: skip
    rev = {"type": "revise", "ref_datetime": "2026-11-05T21:00:00+09:00", "participants": ["민준"], "existing_events": [old],
           "dialogue": [{"speaker": "민준", "text": "11/17에 하자", "ts": "2026-11-05T21:00:00+09:00"}],
           "events": [old | {"status": "rejected"}, old | {"date": "2026-11-17"}]}  # fmt: skip
    assert valid(rev) and not valid(rev | {"existing_events": []})
    print("ok")


if __name__ == "__main__":
    demo() if sys.argv[1:] == ["--demo"] else main()
