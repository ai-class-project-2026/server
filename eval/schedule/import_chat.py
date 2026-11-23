"""역할극 단톡 내보내기 → 테스트셋 한 줄 (라벨링 전 상태).

    uv run python eval/schedule/import_chat.py R001 chat.txt
    uv run python eval/schedule/import_chat.py R001 chat.txt --rename "철수=지민,영희=유나"

카드(cards.jsonl)에서 type, 기준일, 참여자, 기존 일정을 가져와 붙이고
data/schedule/real_test/unlabeled.jsonl 끝에 한 줄 추가한다.
"events"는 비어 있다. 라벨러 두 명이 이 파일을 각자 복사해서
(labels_a.jsonl, labels_b.jsonl) guideline.md대로 채운다.

읽을 수 있는 대화 형식 (한 줄에 메시지 하나):
  카카오톡 PC:     [지민] [오후 9:05] 화요일 어때?
  카카오톡 모바일: 2026. 10. 12. 오후 9:05, 지민 : 화요일 어때?
  직접 적기:       지민: 화요일 어때?
날짜 구분선, 입장/퇴장 안내처럼 형식이 안 맞는 줄은 건너뛰고 몇 줄 건너뛰었는지 알려 준다.
줄바꿈이 들어간 긴 메시지는 바로 앞 메시지에 이어 붙인다.
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

CARDS = Path("data/schedule/real_test/cards/cards.jsonl")
OUT = Path("data/schedule/real_test/unlabeled.jsonl")

PC = re.compile(r"^\[(?P<who>[^\]]+)\] \[(?P<ap>오전|오후) (?P<h>\d{1,2}):(?P<m>\d{2})\] (?P<text>.*)$")
MOBILE = re.compile(
    r"^(?P<y>\d{4})\. (?P<mo>\d{1,2})\. (?P<d>\d{1,2})\. (?P<ap>오전|오후) (?P<h>\d{1,2}):(?P<m>\d{2}), (?P<who>[^:]+?) : (?P<text>.*)$"
)
PLAIN = re.compile(r"^(?P<who>[^:\[\]]{1,20}?)\s*:\s*(?P<text>.+)$")
# 카톡 PC 날짜 구분선: "--------------- 2026년 10월 12일 월요일 ---------------"
PC_DAY = re.compile(r"(\d{4})년 (\d{1,2})월 (\d{1,2})일")


def _hour(ap: str, h: str) -> int:
    h = int(h) % 12
    return h + 12 if ap == "오후" else h


def parse_chat(text: str, ref: dt.datetime) -> tuple[list[dict], int]:
    turns: list[dict] = []
    skipped = 0
    day = ref.date()  # 날짜 구분선이 없으면 기준일 날짜로 본다
    tz = ref.tzinfo
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("-") and (m := PC_DAY.search(line)):
            day = dt.date(*map(int, m.groups()))
            continue
        if m := MOBILE.match(line):
            g = m.groupdict()
            ts = dt.datetime(int(g["y"]), int(g["mo"]), int(g["d"]), _hour(g["ap"], g["h"]), int(g["m"]), tzinfo=tz)
        elif m := PC.match(line):
            g = m.groupdict()
            ts = dt.datetime.combine(day, dt.time(_hour(g["ap"], g["h"]), int(g["m"])), tzinfo=tz)
        elif m := PLAIN.match(line):
            g, ts = m.groupdict(), None
        else:
            if turns and not PC_DAY.search(line) and "님이" not in line:
                turns[-1]["text"] += "\n" + line  # 여러 줄 메시지의 다음 줄
            else:
                skipped += 1
            continue
        turns.append({"speaker": g["who"].strip(), "text": g["text"].strip(), "ts": ts.isoformat() if ts else None})
    return turns, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("card_id")
    ap.add_argument("chat")
    ap.add_argument("--rename", default="", help='닉네임 바꾸기, 예: "철수=지민,영희=유나"')
    ap.add_argument("--cards", default=str(CARDS))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    cards = {c["card_id"]: c for c in map(json.loads, Path(args.cards).read_text(encoding="utf-8").splitlines())}
    card = cards.get(args.card_id)
    if card is None:
        sys.exit(f"카드 {args.card_id}가 {args.cards}에 없다")

    out = Path(args.out)
    if out.exists() and any(
        json.loads(l).get("card_id") == args.card_id for l in out.read_text(encoding="utf-8").splitlines() if l.strip()
    ):
        sys.exit(f"{args.card_id}는 이미 {out}에 있다. 다시 넣으려면 그 줄을 먼저 지우기")

    ref = dt.datetime.fromisoformat(card["ref_datetime"])
    turns, skipped = parse_chat(Path(args.chat).read_text(encoding="utf-8-sig"), ref)
    names = dict(pair.split("=", 1) for pair in args.rename.split(",") if "=" in pair)
    for t in turns:
        t["speaker"] = names.get(t["speaker"], t["speaker"])

    if not turns:
        sys.exit("메시지를 하나도 못 읽었다. 파일 형식을 확인하기 (맨 위 설명 참고)")
    unknown = sorted({t["speaker"] for t in turns} - set(card["participants"]))
    if unknown:
        sys.exit(f"카드 참여자가 아닌 이름: {unknown}. --rename으로 바꾸기 (참여자: {card['participants']})")
    stamped = [dt.datetime.fromisoformat(t["ts"]) for t in turns if t["ts"]]
    if stamped and max(stamped).date() != ref.date():
        # 카톡에는 실제로 역할극을 한 날짜가 찍힌다. 메시지 간격은 그대로 두고,
        # 마지막 메시지가 카드의 기준 시각에 오도록 전체를 옮긴다.
        shift = ref - max(stamped)
        for t in turns:
            if t["ts"]:
                t["ts"] = (dt.datetime.fromisoformat(t["ts"]) + shift).isoformat()
        print(f"참고: 대화 시각을 카드 기준 시각({ref:%m/%d %H:%M})에 맞춰 옮겼다")

    record = {
        "card_id": card["card_id"],
        "type": card["type"],
        "ref_datetime": card["ref_datetime"],
        "participants": card["participants"],
        "dialogue": turns,
        "existing_events": card["existing_events"],
        "events": [],
    }
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"{args.card_id}: {len(turns)}턴 → {out}" + (f" (형식 안 맞는 줄 {skipped}개 건너뜀)" if skipped else ""))


if __name__ == "__main__":
    main()
