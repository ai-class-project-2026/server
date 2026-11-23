"""Get predictions for a test set (feed the output to score.py).

# our model, through the running server (make dev + SCHEDULE_LLM_URL set)
python eval/schedule/run.py data/schedule/pilot/pilot.jsonl preds.jsonl --target server
# baseline A: commercial LLM zero-shot with the exact same prompt
uv run --group ml python eval/schedule/run.py data/schedule/pilot/pilot.jsonl preds_claude.jsonl --target claude
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path[:0] = ["backend", "ml/common"]
os.environ.setdefault("JWT_SECRET", "eval-only-placeholder-secret-0000000")
from pydantic import BaseModel

from app.schedule.prompt import build_messages
from app.schemas import ScheduleEvent, ScheduleRequest


class Out(BaseModel):
    events: list[ScheduleEvent]


def via_server(req: ScheduleRequest, url: str) -> list[dict]:
    import httpx

    r = httpx.post(
        f"{url}/schedule",
        json=req.model_dump(mode="json"),
        headers={"X-API-Key": os.environ.get("AI_API_KEY", "")},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["events"]


def via_claude(req: ScheduleRequest, model: str) -> list[dict]:
    import anthropic

    client = via_claude.client = (
        getattr(via_claude, "client", None) or anthropic.Anthropic()
    )
    system, user = build_messages(req)
    resp = client.beta.messages.parse(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system["content"],
        messages=[user],
        output_format=Out,
    )
    if resp.parsed_output is None:  # refusal / truncated → counts as "no events"
        return []
    return [e.model_dump(mode="json") for e in resp.parsed_output.events]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("testset")
    ap.add_argument("out")
    ap.add_argument("--target", choices=["server", "claude"], required=True)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--model", default="claude-opus-5-5")
    args = ap.parse_args()

    with open(args.out, "w", encoding="utf-8") as f:
        for line in Path(args.testset).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            req = ScheduleRequest.model_validate_json(line)
            t = time.perf_counter()
            events = (
                via_server(req, args.url)
                if args.target == "server"
                else via_claude(req, args.model)
            )
            ms = round((time.perf_counter() - t) * 1000)
            f.write(
                json.dumps({"events": events, "latency_ms": ms}, ensure_ascii=False)
                + "\n"
            )
            print(f"{ms}ms {len(events)} events", file=sys.stderr)


if __name__ == "__main__":
    main()
