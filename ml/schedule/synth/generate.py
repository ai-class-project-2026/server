"""Steps 2-4 of plan 8.6: scenario → LLM-written group chat → checks → jsonl.

    uv run python ml/schedule/synth/generate.py --n 40 --out data/schedule/synth/try.jsonl

Two independent Codex calls per chat. One writes it from the scenario. The other
reads it through the real serving prompt without seeing the answer, and the chat
is kept only if both agree, so unclear or off-script chats are dropped.

Needs a logged-in Codex CLI. Token use is printed at the end; try --n 40 first.
Output is appended, so use a new --seed when adding to an existing file. Each
output line is a ScheduleRequest + gold "events", the same shape
eval/schedule/score.py and train_lora.py read.
"""

import argparse
import collections
import datetime as dt
import json
import os
import random
import sys
from pathlib import Path

from pydantic import BaseModel

sys.path[:0] = [str(Path(__file__).parent), "backend", "ml/common"]
os.environ.setdefault("JWT_SECRET", "training-only-placeholder-secret-000")
import scenarios
from app.schedule.prompt import build_messages
from app.schemas import ScheduleRequest
from codex_json import ask_all
from dateparse_ko import parse

PROMPT = """너는 고등학생 팀플 단톡 대화를 쓰는 작가야. 실제 애들이 친 것처럼 써.
- 기준일: {ref}({weekday})
- 참여자: {people}
- 이 순서대로 일어나는 대화야:
{flow}
- 작은따옴표 안의 날짜, 시간, 장소, 일정 이름은 글자 그대로 한 번 이상 써. 다른 날짜나 시간을 새로 꺼내지 마
- 위 흐름에 없는 동의, 반대, 불참, 결론을 지어내지 마
- 말투: {style}
- 동의할 때는 이런 느낌으로 사람마다 다르게: {agree}
- {small_talk}
- {confirm_rule}
- '근데'는 쓰지 마. 같은 문장을 반복하지 마
- 전체 {turns}턴 안팎"""


class Turn(BaseModel):
    speaker: str
    text: str


class Dialogue(BaseModel):
    turns: list[Turn]


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["turns"],
    "properties": {
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["speaker", "text"],
                "properties": {
                    "speaker": {"type": "string"},
                    "text": {"type": "string"},
                },
            },
        }
    },
}

JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["events"],
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "date", "weekday", "start", "time_text", "location",
                             "status", "attendees", "absent", "note"],
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string"},
                    "weekday": {"type": ["string", "null"]},
                    "start": {"type": ["string", "null"]},
                    "time_text": {"type": ["string", "null"]},
                    "location": {"type": ["string", "null"]},
                    "status": {"enum": ["proposed", "rejected", "confirmed", "cancelled"]},
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "absent": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": ["string", "null"]},
                },
            },
        }
    },
}  # fmt: skip


def to_prompt(sc: dict) -> str:
    ref = dt.date.fromisoformat(sc["ref_date"])
    talk = sc["small_talk"]
    return PROMPT.format(
        ref=ref,
        weekday=scenarios.WEEKDAYS[ref.weekday()],
        people=", ".join(sc["participants"]),
        flow="\n".join(f"  {i}. {b}" for i, b in enumerate(sc["beats"], 1)),
        style=sc["style"],
        agree=", ".join(sc["agree_words"]),
        small_talk=f"중간에 '{talk}' 잡담을 자연스럽게 섞어" if talk else "잡담 없이 일정 얘기만 해",
        confirm_rule="'확정'이라는 단어는 결론 낼 때 한 번만 써"
        if sc["says_confirm"]
        else "'확정'이라는 단어는 절대 쓰지 마",
        turns=sc["turns"],
    )


def check(sc: dict, turns: list[Turn]) -> str | None:
    """Why this chat doesn't match its own label, or None when it does."""
    if not sc.get("min_turns", 5) <= len(turns) <= sc.get("max_turns", 32):
        return "length"
    if {t.speaker for t in turns} - set(sc["participants"]):
        return "unknown speaker"
    said = "".join(t.text for t in turns).replace(" ", "")
    if any(w.replace(" ", "") not in said for w in sc["must"]):
        return "missing expression"
    if not sc["says_confirm"] and "확정" in said:
        return "says 확정"
    ref = dt.date.fromisoformat(sc["ref_date"])
    found = {m.date.isoformat() for t in turns for m in parse(t.text, ref) if m.date}
    if any(e["date"] not in found for e in sc["events"]):
        return "date not resolved"
    return None


def to_record(sc: dict, turns: list[Turn]) -> dict:
    ref = dt.date.fromisoformat(sc["ref_date"])
    now = dt.datetime.combine(ref, dt.time(), dt.timezone(dt.timedelta(hours=9)))
    now += dt.timedelta(minutes=sc["start_minute"])
    dialogue = []
    for t, gap in zip(turns, sc["gaps"]):
        now += dt.timedelta(minutes=gap)
        dialogue.append({"speaker": t.speaker, "text": t.text, "ts": now.isoformat()})
    return {
        "type": sc["type"],
        "ref_datetime": dialogue[-1]["ts"],
        "participants": sc["participants"],
        "dialogue": dialogue,
        "existing_events": sc["existing_events"],
        "events": sc["events"],
    }


def judge_prompt(item: tuple[dict, dict]) -> str:
    messages = build_messages(ScheduleRequest.model_validate(item[1]))
    return "\n\n".join(m["content"] for m in messages)


def _keys(events: list[dict], existing: list[dict]) -> set:
    """What the outcome must match. A rejected idea or an untouched calendar entry
    may be listed or left out, so neither counts."""
    known = {(e["date"], (e["start"] or "")[:5]) for e in existing}
    return {
        (e["date"], (e.get("start") or "")[:5], e["status"], tuple(sorted(e["absent"])))
        for e in events
        if e["status"] != "rejected"
        and not (e["status"] == "confirmed" and (e["date"], (e.get("start") or "")[:5]) in known)
    }


def agrees(record: dict, answer: dict) -> bool:
    existing = record["existing_events"]
    # A dropped time may be reported as rejected or cancelled; both mean it's off.
    dropped = {(e["date"], (e["start"] or "")[:5]) for e in record["events"] if e["status"] == "rejected"}
    said = [
        e
        for e in answer["events"]
        if not (e["status"] == "cancelled" and (e["date"], (e.get("start") or "")[:5]) in dropped)
    ]
    return _keys(record["events"], existing) == _keys(said, existing)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--out", default="data/schedule/synth/train_v2.jsonl")
    ap.add_argument("--model", default="gpt-6.1-sol")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--chunk", type=int, default=200)
    ap.add_argument("--short", action="store_true", help="2~4 turn chats that often omit title, place or time")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    make = scenarios.make_short if args.short else scenarios.make
    todo = [make(rng) for _ in range(args.n)]
    why = collections.Counter()
    kept = tokens_in = tokens_out = 0
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    def spend(usage):
        nonlocal tokens_in, tokens_out
        tokens_in += usage.get("input_tokens", 0)
        tokens_out += usage.get("output_tokens", 0)

    with open(args.out, "a", encoding="utf-8") as f:
        for i in range(0, len(todo), args.chunk):
            written = []
            for sc, answer, usage in ask_all(
                args.model, todo[i : i + args.chunk], to_prompt, SCHEMA, args.workers
            ):
                spend(usage)
                try:
                    turns = Dialogue.model_validate(answer).turns
                except ValueError:  # no answer, or not the shape we asked for
                    why["no answer"] += 1
                    continue
                if reason := check(sc, turns):
                    why[reason] += 1
                else:
                    written.append((sc, to_record(sc, turns)))
            if ask_all.stopped:
                break
            for (sc, record), answer, usage in ask_all(
                args.model, written, judge_prompt, JUDGE_SCHEMA, args.workers
            ):
                spend(usage)
                if answer is None:
                    why["no judge answer"] += 1
                elif agrees(record, answer):
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    kept += 1
                else:
                    why[f"judge disagrees: {sc['type']}"] += 1
            f.flush()
            print(f"{min(i + args.chunk, len(todo))}/{len(todo)} scenarios, kept {kept}", flush=True)
            if ask_all.stopped:
                break
    print(f"kept {kept}, dropped {dict(why)}, tokens in {tokens_in} out {tokens_out} → {args.out}")
    if ask_all.stopped:
        sys.exit("stopped after 10 failures in a row")


def demo():
    rng = random.Random(0)
    counts = collections.Counter()
    for _ in range(3000):
        sc = scenarios.make(rng)
        counts[sc["type"]] += 1
        ref = dt.date.fromisoformat(sc["ref_date"])
        assert "{" not in to_prompt(sc)
        for e in sc["events"]:
            assert (e["start"] is None) == (e["time_text"] is not None), e
            assert not set(e["attendees"]) & set(e["absent"]), e
            assert dt.date.fromisoformat(e["date"]) >= ref, e
        ScheduleRequest.model_validate(to_record(sc, [Turn(speaker=sc["participants"][0], text="ㅇㅋ")] * 6))
    assert set(counts) == set(scenarios.TYPES)
    sc = {"type": "confirm", "ref_date": "2026-10-14", "participants": ["민준", "서연"], "must": ["금", "7시"],
          "says_confirm": False, "events": [{"date": "2026-10-16"}]}  # fmt: skip
    good = [Turn(speaker="민준", text="금 7시 ㄱ?")] * 5
    assert check(sc, good) is None
    assert check(sc, [Turn(speaker="민준", text="금 7시 확정")] * 5) == "says 확정"
    assert check(sc, [Turn(speaker="누구", text="금 7시")] * 5) == "unknown speaker"
    assert check(sc, [Turn(speaker="민준", text="금 8시")] * 5) == "missing expression"
    ev = {"date": "2026-10-16", "start": "19:00", "status": "confirmed", "absent": []}
    rec = {"events": [ev], "existing_events": []}
    assert agrees(rec, {"events": [ev | {"title": "다른 이름"}]})
    assert agrees(rec, {"events": [ev, ev | {"date": "2026-10-10", "status": "rejected"}]})
    assert not agrees(rec, {"events": [ev | {"status": "proposed"}]})
    assert not agrees(rec, {"events": [ev | {"start": "20:00"}]})
    assert agrees({"events": [], "existing_events": [ev]}, {"events": [ev]})  # untouched calendar entry
    old = ev | {"date": "2026-10-15", "status": "rejected"}
    moved = {"events": [old, ev], "existing_events": [old | {"status": "confirmed"}]}
    assert agrees(moved, {"events": [old | {"status": "cancelled"}, ev]})  # old time reported as cancelled
    assert not agrees(moved, {"events": [old | {"status": "cancelled"}]})  # new time is missing
    print("ok", dict(counts))
    focus_demo()


def focus_demo():
    """scenarios.make_focus(): the checks above, plus focus_errors() on every gold, and
    proof that focus_errors() and agrees() reject golds and answers that are wrong."""
    import copy

    rng = random.Random(1)
    counts, by_kind = collections.Counter(), {}
    for _ in range(3000):
        sc = scenarios.make_focus(rng)
        counts[sc["type"]] += 1
        by_kind.setdefault(sc["variant"], sc)
        ref = dt.date.fromisoformat(sc["ref_date"])
        assert "{" not in to_prompt(sc)
        for e in sc["events"]:
            assert not (e["start"] and e["time_text"]), e  # both None is fine: some chats name no time
            assert not set(e["attendees"]) & set(e["absent"]), e
            assert dt.date.fromisoformat(e["date"]) >= ref, e
        assert not (errs := scenarios.focus_errors(sc)), (errs, sc)
        record = to_record(sc, [Turn(speaker=sc["participants"][0], text="ㅇㅋ")] * sc["max_turns"])
        ScheduleRequest.model_validate(record)
        assert {t["ts"][:10] for t in record["dialogue"]} == {sc["ref_date"]}, sc  # the chat stays on one day
        assert agrees(record, {"events": sc["events"]})
        if sc["variant"] not in ("short_not_schedule",) and not sc["variant"].endswith("refuse"):
            # agrees() can only drop a wrong judge answer if the gold has something to match
            assert _keys(sc["events"], sc["existing_events"]), sc
        for i, e in enumerate(sc["events"]):  # a judge that reads a hedge, a refusal or a pending change as a yes
            if e["status"] == "proposed" or (e["status"] == "rejected" and sc["variant"].endswith("refuse")):
                wrong = copy.deepcopy(sc["events"])
                wrong[i]["status"] = "confirmed"
                assert not agrees(record, {"events": wrong}), sc
    assert {"hedge", "time_shift", "constraint", "same_weekday"} <= set(counts), counts
    assert counts["hedge"] > counts["time_shift"] > counts["constraint"] > counts["same_weekday"], counts

    def fails(variant, change, why):
        """Break one generated gold the way a buggy builder would; focus_errors() must say why."""
        sc = copy.deepcopy(by_kind[variant])
        change(sc)
        errs = scenarios.focus_errors(sc)
        assert any(why in e for e in errs), (variant, why, errs)

    def same_start(sc):
        sc["events"][1]["start"] = sc["events"][0]["start"]

    def constraint_in_must(sc):
        sc["must"].append(sc["cues"][0][0])

    def event_at_constraint(sc):
        t = sorted(scenarios._times_in(sc["cues"][0][0], dt.date.fromisoformat(sc["ref_date"])))[0]
        sc["events"].insert(0, sc["events"][-1] | {"start": t, "status": "rejected", "attendees": []})

    def swap_statuses(sc):
        a, b = sc["events"]
        a["status"], b["status"] = b["status"], a["status"]

    def wrong_way(sc):
        sc["shift"][0] = "earlier"

    def after_leaving(sc):
        sc["events"][-1]["start"], sc["must"][-1] = "23:00", "밤 11시"

    fails("later_agree", same_start, "share a start")
    fails("change_refuse", swap_statuses, "gold statuses")
    fails("earlier_agree", swap_statuses, "gold statuses")
    fails("later_short_agree", wrong_way, "wrong way")
    fails("late_reject", constraint_in_must, "constraint time is an event time or a must expression")
    fails("first", constraint_in_must, "constraint time is an event time or a must expression")
    fails("late_reject", event_at_constraint, "constraint time is an event time")
    fails("from", event_at_constraint, "constraint time is an event time")
    fails("window", after_leaving, "outside what the constraints allow")
    fails("chain", lambda sc: sc["events"][0].update(status="confirmed"), "hedge chain gold")
    fails("resolved", lambda sc: sc["events"][0].update(status="proposed"), "hedge resolved gold")
    fails("chain", lambda sc: sc["cues"].append(["오 괜찮을 수도? 물어보고"]), "reads as a date")  # 수 = 수요일?
    fails("bare_confirmed", lambda sc: sc["events"][0].update(date=sc["ref_date"]), "same weekday")
    fails("this_week_confirmed", lambda sc: sc["must"].__setitem__(0, sc["must"][0].replace("이번", "다음")),
          "doesn't resolve")  # fmt: skip
    fails("chain", lambda sc: sc["events"][0].update(date="2026-01-01"), "past date")
    fails("mixed", lambda sc: sc["events"][0].update(absent=sc["participants"][:1]), "attendees")
    fails("two_turn", lambda sc: sc["must"].append("다음주 월"), "no event's date")
    print("focus ok", dict(counts))


if __name__ == "__main__":
    demo() if sys.argv[1:] == ["--demo"] else main()
