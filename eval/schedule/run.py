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
import tempfile
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
    # 서버 응답도 같은 스키마(Out)로 검사한다. 모양이 틀리면 예외 → 예측 파일에 쓰지 않고 멈춘다
    return [e.model_dump(mode="json") for e in Out.model_validate(r.json()).events]


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
    u = getattr(resp, "usage", None)  # 토큰 수는 점수 계산(score.py)이 비용을 낼 때 쓴다
    via_claude.last_usage = (
        {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens} if u else None
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
    ap.add_argument("--price-in", type=float, help="입력 100만 토큰당 달러 (주면 cost_usd를 기록)")
    ap.add_argument("--price-out", type=float, help="출력 100만 토큰당 달러")
    args = ap.parse_args()

    src, dst = Path(args.testset), Path(args.out)
    # 입력과 출력이 같은 파일(같은 경로, 심볼릭/하드 링크 포함)이면 쓰기 전에 거부한다
    if dst.exists() and os.path.samefile(src, dst):
        sys.exit(f"입력과 출력이 같은 파일이에요: {src}. 출력 이름을 바꿔 주세요")
    lines = [ln for ln in src.read_text(encoding="utf-8").splitlines() if ln.strip()]
    reqs = [ScheduleRequest.model_validate_json(ln) for ln in lines]  # 시작 전에 입력 전체를 검사

    # 임시 파일에 쓰고, 끝까지 성공했을 때만 기존 출력을 바꾼다 (중간에 실패해도 이전 결과가 남는다)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=dst.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for req in reqs:
                via_claude.last_usage = None
                t = time.perf_counter()
                events = (
                    via_server(req, args.url)
                    if args.target == "server"
                    else via_claude(req, args.model)
                )
                ms = round((time.perf_counter() - t) * 1000)
                row = {"events": events, "latency_ms": ms}
                usage = via_claude.last_usage if args.target == "claude" else None
                if usage:  # 서버(/schedule)는 토큰 수를 알려 주지 않아서 claude일 때만 남는다
                    row["usage"] = usage
                    if args.price_in is not None and args.price_out is not None:
                        row["cost_usd"] = (
                            usage["input_tokens"] * args.price_in
                            + usage["output_tokens"] * args.price_out
                        ) / 1e6
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"{ms}ms {len(events)} events", file=sys.stderr)
        os.replace(tmp, dst)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()
