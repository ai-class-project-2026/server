"""역할극 단톡 내보내기 → 테스트셋 한 줄 (라벨링 전 상태).

    uv run python eval/schedule/import_chat.py R001 chat.txt
    uv run python eval/schedule/import_chat.py R001 chat.txt --rename "철수=지민,영희=유나"

카드(cards.jsonl)에서 type, 기준일, 참여자, 기존 일정을 가져와 붙이고
아래 파일 끝에 한 줄 추가한다. 어느 파일인지는 카드 번호의 첫 글자로 정해진다 (cards.py의 SETS).
  R001...: data/schedule/real_test/unlabeled.jsonl        (실제 테스트셋)
  P001...: data/schedule/pilot/unlabeled.jsonl            (파일럿)
  H001...: data/schedule/real_test/hard_unlabeled.jsonl   (약점)
"events"는 비어 있다. 라벨러 두 명이 이 파일을 각자 복사해서
(labels_a.jsonl, labels_b.jsonl) guideline.md대로 채운다.

읽을 수 있는 대화 형식 (한 줄에 메시지 하나):
  카카오톡 PC:     [지민] [오후 9:05] 화요일 어때?
  카카오톡 모바일: 2026. 10. 12. 오후 9:05, 지민 : 화요일 어때?
  안드로이드:      2026년 10월 12일 오후 9:05, 지민 : 화요일 어때?
  카톡 PC 영어:    [지민] [9:05 PM] 화요일 어때?
  직접 적기:       지민: 화요일 어때?
날짜 구분선, 입장/퇴장 안내처럼 형식이 안 맞는 줄은 건너뛰고 몇 줄 건너뛰었는지 알려 준다.
줄바꿈이 들어간 긴 메시지는 바로 앞 메시지에 이어 붙인다. 시각이 붙은 형식(위 4가지)으로 된 파일에서는
"서연: ㅇㅋ" 같은 줄도 앞 메시지의 이어지는 줄이다. 시각이 말이 안 되는 줄(오후 13:05)은 건너뛴다.
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from cards import SETS  # 같은 폴더의 cards.py

# 카드 번호 첫 글자 → (카드 파일, 대화를 쌓는 파일)
PLACES = {letter: (f"{folder}/cards.jsonl", out) for letter, _, _, folder, out in SETS.values()}

# 시각: "오후 9:05" 또는 영어 로케일 "9:05 PM"
TIME = r"(?:(?:오전|오후) \d{1,2}:\d{2}|\d{1,2}:\d{2} [AaPp][Mm])"
# 카톡 PC: "[이름] [시각] 내용". 이름에 [ ] : 가 들어갈 수 있어서 "] [시각]" 위치로 이름과 내용을 나눈다
PC_SEP = re.compile(rf"\] \[(?P<time>{TIME})\] ?")
# 모바일/안드로이드: "날짜 시각, 이름 : 내용". 이름에 ":"가 들어갈 수 있어서 " : " 위치로 나눈다
MOBILE = re.compile(rf"^(?P<y>\d{{4}})\. (?P<mo>\d{{1,2}})\. (?P<d>\d{{1,2}})\. (?P<time>{TIME}), (?P<rest>.*)$")
ANDROID = re.compile(rf"^(?P<y>\d{{4}})년 (?P<mo>\d{{1,2}})월 (?P<d>\d{{1,2}})일 (?P<time>{TIME}), (?P<rest>.*)$")
# 시각 모양이 깨진 머리줄("오후 9:8", "오후 9:")도 "머리줄 모양"으로 알아본다. 안 그러면 앞 메시지의 이어지는 줄이 돼 버린다
TIME_LOOSE = r"(?:(?:오전|오후) \d{1,2}:\d{0,2}|\d{1,2}:\d{0,2} ?[AaPp][Mm])"
LOOSE_PC = re.compile(rf"^\[.+\] \[{TIME_LOOSE}\]")
LOOSE_DATED = re.compile(rf"^(?:\d{{4}}\. \d{{1,2}}\. \d{{1,2}}\.|\d{{4}}년 \d{{1,2}}월 \d{{1,2}}일) {TIME_LOOSE}, ")
PLAIN = re.compile(r"^(?P<who>[^:\[\]]{1,20}?)\s*:\s*(?P<text>.+)$")
# 줄 전체가 이 모양일 때만 안내 줄이다 (사람이 쓴 말 속에 비슷한 글이 있어도 지우지 않는다)
META = re.compile(r"^(?:저장한 날짜\s*:.*|Date Saved\s*:.*|.+ 님과 카카오톡 대화)$")
SYSTEM = re.compile(
    r"^(?:메시지가 삭제되었습니다\.?|삭제된 메시지입니다\.?|.+님이 (?:들어왔습니다|나갔습니다)\.?|"
    r".+님을 초대했습니다\.?|.+님이 .+님을 초대했습니다\.?|.+님이 메시지를 삭제했습니다\.?)$"
)
# 날짜 구분선: "--- 2026년 10월 12일 월요일 ---", "--- Monday, October 12, 2026 ---", 안드로이드 "2026년 10월 12일 월요일"
PC_DAY = re.compile(r"^-+\s*(\d{4})년 (\d{1,2})월 (\d{1,2})일.*?-+$")
EN_DAY = re.compile(r"^-+\s*(?:[A-Za-z]+, )?([A-Za-z]+) (\d{1,2}), (\d{4})\s*-+$")
ANDROID_DAY = re.compile(r"^(\d{4})년 (\d{1,2})월 (\d{1,2})일(?: [월화수목금토일]요일)?$")
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}


def _clock(raw: str) -> dt.time | None:
    """12시간제 시각 → time. 시(1~12)나 분(0~59)이 말이 안 되면 None (조용히 고쳐 쓰지 않는다)."""
    if m := re.fullmatch(r"(오전|오후) (\d{1,2}):(\d{2})", raw):
        pm, h, mi = m[1] == "오후", int(m[2]), int(m[3])
    elif m := re.fullmatch(r"(\d{1,2}):(\d{2}) ([AaPp])[Mm]", raw):
        pm, h, mi = m[3] in "Pp", int(m[1]), int(m[2])
    else:
        return None
    if not (1 <= h <= 12 and 0 <= mi <= 59):
        return None
    return dt.time(h % 12 + (12 if pm else 0), mi)


def _pick(cands: list[tuple], speakers: set[str] | None):
    """이름 후보가 여럿이면 참여자 목록에 있는 이름을 고르고, 없으면 첫 후보."""
    if speakers:
        for c in cands:
            if c[0].strip() in speakers:
                return c
    return cands[0]


def _header(line: str, day: dt.date, tz, speakers):
    """메시지 머리줄이면 (이름, 내용, 시각), 머리줄 모양인데 시각이 이상하면 "bad", 아니면 None."""
    cands, shape = [], False
    if line.startswith("["):
        for m in PC_SEP.finditer(line):
            shape = True
            if (clock := _clock(m["time"])) is not None:
                cands.append((line[1 : m.start()], line[m.end() :], dt.datetime.combine(day, clock, tzinfo=tz)))
    elif m := (MOBILE.match(line) or ANDROID.match(line)):
        shape = True
        try:
            d, clock = dt.date(int(m["y"]), int(m["mo"]), int(m["d"])), _clock(m["time"])
        except ValueError:
            clock = None
        if clock is not None:
            rest, pos = m["rest"], -1
            while (pos := rest.find(" : ", pos + 1)) >= 0:
                cands.append((rest[:pos], rest[pos + 3 :], dt.datetime.combine(d, clock, tzinfo=tz)))
            if not cands:
                return None  # "날짜 시각, 내용"만 있고 이름이 없다: 머리줄이 아니다
    cands = [c for c in cands if c[0].strip()]
    if cands:
        return _pick(cands, speakers)
    return "bad" if shape or LOOSE_PC.match(line) or LOOSE_DATED.match(line) else None


def parse_chat(
    text: str, ref: dt.datetime, speakers: set[str] | None = None, bad_lines: list[str] | None = None
) -> tuple[list[dict], int]:
    """speakers: 말한 사람으로 인정할 이름들. 이름에 ":" 등이 들어 있을 때 어디서 자를지 고르는 데도 쓴다.
    머리줄(시각이 붙은 줄)이 하나라도 있는 파일은 "내보내기 형식"이다. 이때는 "서연: ㅇㅋ" 같은 줄도
    앞 메시지의 이어지는 줄이고, 직접 적기("이름: 내용") 해석은 머리줄이 없는 파일에서만 한다.
    시각이 이상한 머리줄(오후 13:05 등)은 건너뛰고 bad_lines에 담는다."""
    lines = text.splitlines()
    tz = ref.tzinfo
    day = ref.date()  # 날짜 구분선이 없으면 기준일 날짜로 본다
    # 시각이 이상한 머리줄("bad")만 있는 파일은 내보내기 형식이 아니다. 읽은 머리줄이 있을 때만 그렇게 본다
    structured = any(isinstance(_header(ln.strip(), day, tz, speakers), tuple) for ln in lines)
    turns: list[dict] = []
    skipped = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not turns and META.match(line):
            continue
        if m := PC_DAY.match(line):
            try:
                day = dt.date(*map(int, m.groups()))
            except ValueError:
                skipped += 1
            continue
        if m := EN_DAY.match(line):
            try:
                day = dt.date(int(m[3]), MONTHS[m[1].lower()], int(m[2]))
            except (KeyError, ValueError):
                skipped += 1
            continue
        if m := ANDROID_DAY.match(line):
            try:
                day = dt.date(*map(int, m.groups()))  # 뒤따르는 PC식 머리줄(시각만 있음)이 이 날짜를 쓴다
            except ValueError:
                skipped += 1
            continue
        if SYSTEM.match(line):
            skipped += 1
            continue
        head = _header(line, day, tz, speakers)
        if head == "bad":
            skipped += 1
            if bad_lines is not None:
                bad_lines.append(line)
            continue
        if head:
            who, body, ts = head
            day = ts.date()  # 날짜가 적힌 머리줄(모바일/안드로이드) 뒤의 시각만 있는 PC 머리줄은 이 날짜를 쓴다
            turns.append({"speaker": who.strip(), "text": body.strip(), "ts": ts.isoformat()})
            continue
        if not structured:
            # 직접 적기: 참여자(또는 --rename 이름)가 말한 "이름: 내용"만 새 메시지다
            for k in [i for i, c in enumerate(line) if c == ":"]:
                who, body = line[:k].strip(), line[k + 1 :].strip()
                if who and body and (speakers is None and PLAIN.match(line) or speakers and who in speakers):
                    turns.append({"speaker": who, "text": body, "ts": None})
                    break
            else:
                if turns:
                    turns[-1]["text"] += "\n" + raw.rstrip()
                else:
                    skipped += 1
            continue
        if turns:
            turns[-1]["text"] += "\n" + raw.rstrip()  # 여러 줄 메시지의 다음 줄은 그대로 붙인다
        else:
            skipped += 1
    return turns, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("card_id")
    ap.add_argument("chat")
    ap.add_argument("--rename", default="", help='닉네임 바꾸기, 예: "철수=지민,영희=유나"')
    ap.add_argument("--cards", help="기본: 카드 번호 첫 글자로 정한다")
    ap.add_argument("--out", help="기본: 카드 번호 첫 글자로 정한다")
    args = ap.parse_args()

    cards_default, out_default = PLACES.get(args.card_id[:1], PLACES["R"])
    args.cards, args.out = args.cards or cards_default, args.out or out_default
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
    names = dict(pair.split("=", 1) for pair in args.rename.split(",") if "=" in pair)
    speakers = set(card["participants"]) | set(names) | set(names.values())
    bad_lines: list[str] = []
    turns, skipped = parse_chat(Path(args.chat).read_text(encoding="utf-8-sig"), ref, speakers, bad_lines)
    for line in bad_lines[:5]:
        print(f"시각이 이상해서 건너뜀: {line[:60]}")
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
        **({"weakness": card["weakness"]} if "weakness" in card else {}),
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
