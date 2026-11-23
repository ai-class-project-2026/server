"""Steps 2-4 of plan 8.6: scenario → LLM-written group chat → auto-check → jsonl.

    uv run python ml/schedule/synth/generate.py --n 20 --out data/schedule/synth/try.jsonl

Needs a logged-in Codex CLI. Token use is printed at the end; try --n 20 first.
Output is appended, so use a new --seed when adding to an existing file. Each
output line is a ScheduleRequest + gold "events", the same shape
eval/schedule/score.py and train_lora.py read.
"""

import argparse
import datetime as dt
import json
import random
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

sys.path[:0] = [str(Path(__file__).parent)]
import scenarios

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

# No tools, no user config, and an empty working folder: the writer sees only
# the scenario, so the real test sets can never reach it.
CODEX_ARGS = [
    "--ignore-user-config",
    "--ignore-rules",
    "--ephemeral",
    "--skip-git-repo-check",
    "--json",
    "-s", "read-only",
    "-c", 'model_reasoning_effort="high"',
    "-c", 'service_tier="priority"',  # Codex "fast mode"
    "-c", "suppress_unstable_features_warning=true",
    "-c", "project_doc_max_bytes=0",
    "-c", "include_permissions_instructions=false",
    "-c", "include_apps_instructions=false",
    "-c", "include_collaboration_mode_instructions=false",
    "-c", "include_environment_context=false",
    "-c", 'web_search="disabled"',
    "--enable", "skip_host_skill_discovery",
    "--enable", "omit_skill_descriptions",
    "--disable", "apps",
    "--disable", "plugins",
    "--disable", "multi_agent",
    "--disable", "shell_tool",
    "--disable", "tool_search",
    "--disable", "unified_exec",
]  # fmt: skip


def write(model: str, sc: dict) -> tuple[list[Turn], dict]:
    ref = dt.date.fromisoformat(sc["ref_date"])
    prompt = PROMPT.format(
        ref=ref,
        weekday=scenarios.WEEKDAYS[ref.weekday()],
        people=", ".join(sc["participants"]),
        flow=sc["flow"],
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "schema.json").write_text(json.dumps(SCHEMA))
        (root / "instructions.md").write_text("요청받은 JSON만 출력한다. 도구를 쓰지 않는다.\n")
        run = subprocess.run(
            [
                "codex", "exec", "-m", model, *CODEX_ARGS,
                "-c", f"model_instructions_file={json.dumps(str(root / 'instructions.md'))}",
                "-C", str(root / "work"),
                "--output-schema", str(root / "schema.json"),
                "-o", str(root / "out.json"),
                prompt,
            ],  # fmt: skip
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if run.returncode:
            raise RuntimeError(f"codex exit {run.returncode}: {run.stdout[-300:]}")
        turns = Dialogue.model_validate_json((root / "out.json").read_text()).turns
    usage = {}
    for line in run.stdout.splitlines():
        if line.startswith('{"type":"turn.completed"'):
            usage = json.loads(line)["usage"]
    return turns, usage


def check(sc: dict, turns: list[Turn]) -> bool:
    """Drop chats that don't match their own label."""
    if not 6 <= len(turns) <= 14 or {t.speaker for t in turns} - set(
        sc["participants"]
    ):
        return False
    if not sc["events"]:
        return True
    # The final date must be said the way the scenario wrote it. Small talk like
    # "오늘 급식" is allowed, so don't compare every date found in the chat.
    wanted = sc["date_text"].replace(" ", "")
    return any(wanted in t.text.replace(" ", "") for t in turns)


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
    ap.add_argument("--model", default="gpt-6.1-sol")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    todo = [scenarios.make(rng) for _ in range(args.n)]
    stop = threading.Event()  # a run of failures usually means the quota is gone
    failures = 0

    def one(sc):
        nonlocal failures
        if stop.is_set():
            return sc, None
        try:
            res = write(args.model, sc)
            failures = 0
            return sc, res
        except (subprocess.SubprocessError, RuntimeError, ValueError, OSError) as e:
            print(f"skip: {str(e)[:300]}", file=sys.stderr)
            failures += 1
            if failures >= 10:
                stop.set()
        return sc, None

    kept = dropped = tokens_in = tokens_out = 0
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "a") as f, ThreadPoolExecutor(args.workers) as pool:
        for sc, res in pool.map(one, todo):
            if res is None:
                dropped += 1
                continue
            turns, usage = res
            tokens_in += usage.get("input_tokens", 0)
            tokens_out += usage.get("output_tokens", 0)
            if check(sc, turns):
                f.write(json.dumps(to_record(sc, turns), ensure_ascii=False) + "\n")
                f.flush()
                kept += 1
            else:
                dropped += 1
    print(
        f"kept {kept}, dropped {dropped}, tokens in {tokens_in} out {tokens_out} → {args.out}"
    )
    if stop.is_set():
        sys.exit("stopped after 10 failures in a row")


if __name__ == "__main__":
    main()
