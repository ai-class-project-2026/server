"""Steps 2-4 of plan 8.6: scenario → LLM-written group chat → auto-check → jsonl.

    uv run --group ml python ml/schedule/synth/generate.py --n 20 --out data/schedule/synth/try.jsonl

Needs ANTHROPIC_API_KEY (or an `ant auth login` profile). Cost is printed at the end;
try --n 20 first. Each output line is a ScheduleRequest + gold "events", the
same shape eval/schedule/score.py and train_lora.py read.
"""

import argparse
import datetime as dt
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
from pydantic import BaseModel

sys.path[:0] = [str(Path(__file__).parent), "backend", "ml/common"]
import scenarios

from dateparse_ko import parse

PROMPT = """너는 고등학생 팀플 단톡 대화를 만드는 작가야.
아래 시나리오를 정확히 따르는 대화를 만들어.
- 기준일: {ref}({weekday})
- 참여자: {people}
- 흐름: {flow}
- 날짜는 흐름에 적힌 표현 그대로 써
- 말투: 줄임말(ㅇㅋ, ㄱㄱ, ㄱㄴ), 오타, 짧은 문장, 일정과 상관없는 잡담 1~2턴 섞기
- 6~12턴"""


class Turn(BaseModel):
    speaker: str
    text: str


class Dialogue(BaseModel):
    turns: list[Turn]


def write(
    client: anthropic.Anthropic, model: str, sc: dict
) -> tuple[list[Turn], object] | None:
    ref = dt.date.fromisoformat(sc["ref_date"])
    resp = client.beta.messages.parse(
        model=model,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # a refused sample is retried on another model
        output_config={
            "effort": "low"
        },  # short creative writing; no deep reasoning needed
        output_format=Dialogue,
        messages=[
            {
                "role": "user",
                "content": PROMPT.format(
                    ref=ref,
                    weekday=scenarios.WEEKDAYS[ref.weekday()],
                    people=", ".join(sc["participants"]),
                    flow=sc["flow"],
                ),
            }
        ],
    )
    if resp.stop_reason != "end_turn" or resp.parsed_output is None:
        return None
    return resp.parsed_output.turns, resp.usage


def check(sc: dict, turns: list[Turn]) -> bool:
    """Drop chats that don't match their own label."""
    if not 6 <= len(turns) <= 14 or {t.speaker for t in turns} - set(
        sc["participants"]
    ):
        return False
    ref = dt.date.fromisoformat(sc["ref_date"])
    found = {m.date for t in turns for m in parse(t.text, ref) if m.date}
    gold = {dt.date.fromisoformat(e["date"]) for e in sc["events"]}
    # Only judge when the normalizer recognized something (it grows during part 4).
    return not (found and gold and not gold & found)


def to_record(sc: dict, turns: list[Turn]) -> dict:
    start = dt.datetime.fromisoformat(sc["ref_date"]).replace(
        hour=21, tzinfo=dt.timezone(dt.timedelta(hours=9))
    )
    dialogue = [
        {
            "speaker": t.speaker,
            "text": t.text,
            "ts": (start + dt.timedelta(minutes=i)).isoformat(),
        }
        for i, t in enumerate(turns)
    ]
    return {
        "type": sc["type"],
        "ref_datetime": dialogue[-1]["ts"],
        "participants": sc["participants"],
        "dialogue": dialogue,
        "existing_events": [
            e | {"status": "confirmed"}
            for e in sc["events"]
            if e["status"] == "cancelled"
        ],
        "events": sc["events"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out", default="data/schedule/synth/train.jsonl")
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    todo = [scenarios.make(rng) for _ in range(args.n)]
    client = anthropic.Anthropic()

    def one(sc):
        try:
            return sc, write(client, args.model, sc)
        except anthropic.APIStatusError as e:
            print(f"skip ({e.status_code}): {e.message}", file=sys.stderr)
        except anthropic.APIConnectionError as e:
            print(f"skip (connection): {e}", file=sys.stderr)
        return sc, None

    kept = dropped = tokens_in = tokens_out = 0
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "a") as f, ThreadPoolExecutor(args.workers) as pool:
        for sc, res in pool.map(one, todo):
            if res is None:
                dropped += 1
                continue
            turns, usage = res
            tokens_in += usage.input_tokens
            tokens_out += usage.output_tokens
            if check(sc, turns):
                f.write(json.dumps(to_record(sc, turns), ensure_ascii=False) + "\n")
                kept += 1
            else:
                dropped += 1
    print(
        f"kept {kept}, dropped {dropped}, tokens in {tokens_in} out {tokens_out} → {args.out}"
    )


if __name__ == "__main__":
    main()
