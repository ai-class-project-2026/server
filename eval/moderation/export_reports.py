"""신고된 메시지를 검수용으로 꺼내요 (기획서 9.7 ②③, data/moderation/report_retrain.md).

    uv run python eval/moderation/export_reports.py --db app.db --out data/moderation/reports/2026-11-26.jsonl

DB는 읽기 전용으로 열어요: 이 스크립트는 DB를 바꾸지 않아요.
신고된 메시지 하나에 한 줄:
  {"message_id", "text", "context": [앞 메시지 최대 3개, 오래된 것부터],
   "false_positive": [욕설 아님] 신고 수, "missed": [욕설 신고] 신고 수,
   "is_profane", "profanity_score", "model_version": 당시 판정,
   "label": null, "reviewers": [], "reason": ""}   ← 검수자가 채워요

- AI 분석에 동의한 워크스페이스(ai_enabled_at이 있는 곳)만 꺼내요. 지금 앱에 남는 동의는
  이것뿐이에요. 신고 데이터 사용 동의를 따로 정하면(report_retrain.md 4장) 여기를 바꿔요.
- 익명화: 워크스페이스 멤버 이름과 @멘션 → [이름], 이메일 → [이메일], 전화번호 → [번호].
  user_id, channel_id는 내보내지 않아요. 학교, 주소 같은 장소는 검수자가 보고 [장소]로 바꿔요.
- 저장 폴더 data/moderation/reports/는 git에 올리지 않아요 (실제 사용자 대화).

검수가 끝나면 학습 데이터 형식으로 바꿔요 (9.7 ⑤). label이 0이나 1이고 검수자가 2명 이상인
줄만 넘겨요 (label이 null이거나 "보류"면 빼요):

    uv run python eval/moderation/export_reports.py --to-raw data/moderation/reports/2026-11-26.jsonl \\
        --out data/moderation/raw/reports_20261126.jsonl
"""

import argparse
import json
import re
import sqlite3
from pathlib import Path

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_MENTION = re.compile(r"@\S+")
_PHONE = re.compile(r"\d{2,3}[- .]?\d{3,4}[- .]?\d{4}")


def anonymize(text: str, names: list[str]) -> str:
    text = _EMAIL.sub("[이메일]", text)
    text = _MENTION.sub("[이름]", text)
    text = _PHONE.sub("[번호]", text)
    for name in sorted(names, key=len, reverse=True):  # 긴 이름부터: "김민수"를 "민수"보다 먼저
        if len(name) >= 2:  # 한 글자 이름은 다른 말 속에 너무 흔해요
            text = text.replace(name, "[이름]")
    return text


def export(db: Path, since: str | None) -> list[dict]:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    rows = con.execute(
        """
        SELECT m.id, m.channel_id, m.text, m.is_profane, m.profanity_score, m.model_version,
               c.workspace_id,
               SUM(r.type = 'false_positive'), SUM(r.type = 'missed')
        FROM reports r
        JOIN messages m ON m.id = r.message_id
        JOIN channels c ON c.id = m.channel_id
        JOIN workspaces w ON w.id = c.workspace_id
        WHERE w.ai_enabled_at IS NOT NULL AND (? IS NULL OR r.created_at >= ?)
        GROUP BY m.id
        ORDER BY m.id
        """,
        (since, since),
    ).fetchall()
    names: dict[int, list[str]] = {}
    out = []
    for mid, channel, text, profane, score, version, ws, fp, missed in rows:
        if ws not in names:
            names[ws] = [
                n
                for (n,) in con.execute(
                    "SELECT u.name FROM members mem JOIN users u ON u.id = mem.user_id"
                    " WHERE mem.workspace_id = ?",
                    (ws,),
                )
            ]
        context = [
            t
            for (t,) in con.execute(
                "SELECT text FROM messages WHERE channel_id = ? AND id < ? ORDER BY id DESC LIMIT 3",
                (channel, mid),
            )
        ][::-1]
        out.append(
            {
                "message_id": mid,
                "text": anonymize(text, names[ws]),
                "context": [anonymize(t, names[ws]) for t in context],
                "false_positive": fp,
                "missed": missed,
                "is_profane": bool(profane),
                "profanity_score": score,
                "model_version": version,
                "label": None,
                "reviewers": [],
                "reason": "",
            }
        )
    con.close()
    return out


def to_raw(reviewed: Path) -> tuple[list[dict], int]:
    """검수한 줄 → build_train.py가 읽는 {"text", "label", "context"}. 넘기지 않은 줄 수도 돌려줘요."""
    rows = [json.loads(x) for x in reviewed.read_text(encoding="utf-8").splitlines() if x.strip()]
    ok = [r for r in rows if r.get("label") in (0, 1) and len(set(r.get("reviewers", []))) >= 2]
    return [{"text": r["text"], "label": r["label"], "context": r["context"]} for r in ok], len(rows) - len(ok)


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=Path("app.db"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--since", help="이 날짜(YYYY-MM-DD) 이후 신고만")
    ap.add_argument("--to-raw", type=Path, help="검수한 파일을 학습 데이터 형식으로 바꿔요")
    args = ap.parse_args()
    if args.to_raw:
        rows, skipped = to_raw(args.to_raw)
        _write(args.out, rows)
        bad = sum(r["label"] for r in rows)
        print(f"{len(rows)} reviewed rows (profane {bad}, clean {len(rows) - bad}), skipped {skipped} → {args.out}")
        return
    rows = export(args.db, args.since)
    _write(args.out, rows)
    fp = sum(r["false_positive"] > 0 for r in rows)
    print(f"{len(rows)} reported messages ({fp} false positive, {len(rows) - fp} missed only) → {args.out}")


if __name__ == "__main__":
    main()
