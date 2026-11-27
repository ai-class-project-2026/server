"""v5 scenarios: one written chat, several training rows (DESIGN.md in ~/aip-tools/v5sched).

Each scenario is a list of beats. A beat is ONE act by ONE person (or a narration such as
"C never answers"). The gold after every beat is not written by hand: replay() applies
plan 8.3 to the acts, so a prefix row cut after beat k gets replay(acts[:k]).

    propose sid        new idea                 agree cid / object cid / hedge cid
    counter sid old    "old 안 돼, 대신 sid"     absent cid|Ei / back cid|Ei / late cid|Ei m
    declare cid        proposer's "확정"/"그럼 그때 보자"   drop cid  proposer withdraws it
    revive cid [old]   a rejected idea comes back (old, if given, is dropped)
    place cid p        "장소는 p로" (counts once someone else agrees)
    move sid Ei        change calendar entry i     cancel Ei   cancel calendar entry i
    info / leftover    talk that decides nothing    silent      named people never answer
    instr op           the one-turn "[요청] ..." instruction mode

Rules replay() applies (plan 8.3, plus the choices in DESIGN.md):
  settled = no open objection and (proposer + agreements >= n//2+1, or the proposer
            declared and someone else agreed). Not sticky: a later objection reopens it.
  "못 가, 너네끼리 해" (absent) and "늦게 감" (late) are consent in one reading and not in the
  other; replay() runs both readings and a beat where they differ is `ambiguous` (no cut).
  A place counts only once someone other than its sayer agrees (or the proposer declares);
  in between the beat is ambiguous.
  Calendar entries appear in the output only when the chat changes or cancels them, or a
  member's attendance in them changes.
"""

import datetime as dt
import random

from dateparse_ko import parse
from scenarios import AGREE, HEDGES, NON_NUMERIC, SMALL_TALK, STYLES, WEEKDAYS, _clock, _date_of, _say_clock, _say_date
from scenarios import _need as need, _times_in as times_in

NAMES = """민준 서연 지호 하은 도윤 서준 지우 예린 현우 수아 시우 하린 주원 지민 건우 유나 준서 채원 은우 소율
태윤 다은 승현 나연 연우 윤서 재민 가은 민재 아린 규리 한결 소윤 정우 다인 시온 예준 로아 태오 서아 지안 하람
유찬 세아 이준 수빈 민서 동현 채린 우진 보민 나은 선우 하율 주아 성민 아인 재윤 다현 은호 지율 리안 혜원 준호
서윤 도하 유진 시현 가람 은채 태민 소희 정민 예서""".split()
TITLES = ["국어 발표 준비", "과학 탐구 보고서 회의", "동아리 부스 준비", "영상 촬영", "연극 리허설", "수행평가 PPT 회의",
          "캡스톤 회의", "조별 과제 회의", "역사 신문 만들기", "영어 스피치 연습", "포스터 제작", "실험 보고서 정리",
          "중간 발표 리허설", "자료 조사 회의", "인터뷰 촬영", "축제 공연 연습", "코딩 프로젝트 회의", "설문지 만들기",
          "토론 준비", "최종 점검", "대본 리딩", "편집 회의", "모의 면접 연습", "UCC 촬영", "합창 연습"]  # fmt: skip
# v5 stores a place exactly as said (no 디코 → 디스코드), so a judge can copy it
PLACES = ["도서관 3층", "음악실", "과학실", "컴퓨터실", "학생회실", "스터디카페", "줌", "디스코드", "카페 2층", "운동장",
          "강당", "세미나실", "302호", "동아리방", "학교 도서관", "시청각실", "미술실", "학교 앞 카페"]  # fmt: skip
SETTINGS = ["고등학교 2학년 팀플 단톡", "고등학교 1학년 수행평가 조 단톡", "중학교 3학년 모둠 단톡", "대학교 1학년 교양 조별과제 단톡",
            "대학교 전공 수업 팀 프로젝트 단톡", "고등학교 동아리 부원 단톡", "고3 탐구 보고서 팀 단톡"]  # fmt: skip
EXTERNAL = ["{t}까지 과제 제출이야", "{t}에 모의고사 끝남", "{t}까지 수행평가 업로드래", "{t}에 학원 시험 있음",
            "{t}까지 보고서 메일로 내야 됨"]  # fmt: skip
LEFTOVER = {  # lines that close the previous (already handled) topic
    "cancelled": ["ㅇㅋ 그럼 그거 취소", "ㅇㅇ 취소된 거 확인함", "취소 눌렀어", "아까 그건 없던 걸로 ㅇㅋ"],
    "accepted": ["카드 수락함", "캘린더에 들어갔네", "ㅇㅇ 그날 봐", "일정 등록된 거 확인"],
    "dropped": ["그럼 다음에 다시 얘기하자", "일단 보류", "그건 나중에 정하자"],
}
PAUSE_ACTS = {"propose", "counter", "agree", "object", "hedge", "absent", "back", "late", "declare", "move",
              "cancel", "revive", "leftover", "drop"}  # fmt: skip
OPEN_TONE = ["한두 글자로", "짧게", "", "", "이모티콘 느낌으로 짧게", "이유를 짧게 붙여서"]


def _hm(minutes: int) -> str:
    return _clock(minutes).strftime("%H:%M")


# ── building a scenario ──────────────────────────────────────────────────────


class Ctx:
    """Collects slots, calendar entries and beats while a family builder runs."""

    def __init__(self, rng: random.Random, ref: dt.date, people: list[str]):
        self.rng, self.ref, self.people = rng, ref, people
        self.used: set = set()
        self.slots: dict = {}
        self.existing: list = []
        self.beats: list = []
        self.says_confirm = False
        self.bare_hour = False  # some time is a bare 8~11시 with no context → time words are policed
        self.extra_dates: list = []  # dates said in passing that are no event (leftover, external)

    def day(self, lo=1, hi=16, d=None) -> dt.date:
        if d:
            return d
        while True:
            d = self.ref + dt.timedelta(days=self.rng.choices(range(lo, hi + 1))[0])
            if d not in self.used:
                self.used.add(d)
                return d

    def minutes(self, lo=13 * 60, hi=21 * 60) -> int:
        return self.rng.randrange(lo, hi + 1, 30)

    def slot(self, title=None, d=None, minutes=None, say_time=None, time_text=None, place=False,
             say_title=None, say_date=None, untimed=False) -> str:  # fmt: skip
        """A new idea. place: False = none, True = random, str = that place. untimed → no time is
        said (start and time_text null). say_time overrides how the time is said."""
        rng = self.rng
        d = self.day(d=d)
        if minutes is None and time_text is None and say_time is None and not untimed:
            minutes = self.minutes()
        if minutes is not None and say_time is None:
            say_time = _say_clock(minutes // 60, minutes % 60, rng)
        p = rng.choice(PLACES) if place is True else (place or None)
        sid = f"s{len(self.slots) + 1}"
        self.slots[sid] = {
            "title": title or rng.choice(TITLES), "date": d.isoformat(),
            "start": _hm(minutes) if minutes is not None else None, "time_text": time_text,
            "location": p, "say_date": say_date or _say_date(self.ref, d, rng), "say_time": say_time or time_text or "",
            "say_title": rng.random() < 0.7 if say_title is None else say_title, "label": f"안{len(self.slots) + 1}",
        }  # fmt: skip
        return sid

    def calendar(self, title=None, d=None, minutes=None, place=None, absent=()) -> int:
        """A confirmed calendar entry (existing_events). Returns its index."""
        rng = self.rng
        d = self.day(d=d)
        minutes = self.minutes(11 * 60, 20 * 60) if minutes is None else minutes
        self.existing.append({
            "title": title or rng.choice([t for t in TITLES if t not in {e["title"] for e in self.existing}]),
            "date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()], "start": _hm(minutes), "time_text": None,
            "location": place, "status": "confirmed", "attendees": [p for p in self.people if p not in absent],
            "absent": list(absent), "note": None,
        })  # fmt: skip
        return len(self.existing) - 1

    def moved(self, i: int, d=None, minutes=None, say_time=None, place=None) -> str:
        """A slot that is calendar entry i with some fields changed (for move)."""
        e = self.existing[i]
        sid = f"s{len(self.slots) + 1}"
        same_day = d is None
        d = dt.date.fromisoformat(e["date"]) if same_day else d
        if not same_day:
            self.used.add(d)
        start = _hm(minutes) if minutes is not None else e["start"]
        if minutes is not None and say_time is None:
            say_time = _say_clock(minutes // 60, minutes % 60, self.rng)
        self.slots[sid] = {
            "title": e["title"], "date": d.isoformat(), "start": start, "time_text": None,
            "location": place or e["location"], "say_date": "" if same_day else _say_date(self.ref, d, self.rng),
            "say_time": say_time or "", "say_title": True, "label": f"안{len(self.slots) + 1}", "of": i,
        }  # fmt: skip
        return sid

    def say(self, i: int) -> dict:
        """How the chat names calendar entry i: its date and title (time sometimes)."""
        e = self.existing[i]
        h, m = int(e["start"][:2]), int(e["start"][3:])
        return {"date": _say_date(self.ref, dt.date.fromisoformat(e["date"]), self.rng),
                "time": _say_clock(h, m, self.rng) if 10 <= h <= 22 else "", "title": e["title"]}  # fmt: skip

    def beat(self, who, act: list, say: str, must=(), word=False) -> None:
        self.beats.append({"who": who, "act": act, "say": say, "must": [m for m in must if m], "word": word})
        self.says_confirm |= word

    # what a beat says about a slot
    def pitch(self, sid: str, title=True) -> tuple[str, list[str]]:
        s = self.slots[sid]
        parts = [f"'{s['say_date']}'" if s["say_date"] else "", f"'{s['say_time']}'" if s["say_time"] else ""]
        must = [s["say_date"], s["say_time"]]
        if s["location"]:
            parts.append(f"장소 '{s['location']}'")
            must.append(s["location"])
        if title and s["say_title"]:
            parts.append(f"일정 이름 '{s['title']}'")
            must.append(s["title"])
        elif title:
            parts.append("(무슨 모임인지는 말하지 않음)")
        return " ".join(p for p in parts if p), must

    def _pitch_no_place(self, sid: str) -> tuple[str, list[str]]:
        s = self.slots[sid]
        return " ".join(f"'{x}'" for x in (s["say_date"], s["say_time"]) if x), [s["say_date"], s["say_time"]]

    def ref_(self, sid: str) -> str:
        if sid[0] in "xE":  # a cancellation of / a calendar entry itself
            e = self.existing[int(sid[1:])]
            return f"'{e['title']}' 취소 제안" if sid[0] == "x" else f"캘린더의 '{e['title']}'"
        s = self.slots[sid]
        return f"{s['label']}({' '.join(x for x in (s['say_date'], s['say_time']) if x) or s['title']})"

    # common beat shapes
    def propose(self, who, sid, extra=""):
        text, must = self.pitch(sid)
        self.beat(who, ["propose", sid], f"{self.ref_(sid).split('(')[0]}: {text} 제안{extra}", must)

    def counter(self, who, sid, old, extra=""):
        new, before = self.slots[sid], self.slots[old]
        if not new["location"] and before["location"]:  # "4시는?" keeps the place already on the table
            new["location"] = before["location"]
            text, must = self._pitch_no_place(sid)
            extra = " (장소는 그대로라 장소 말은 안 해도 됨)" + extra
        else:
            text, must = self.pitch(sid, title=False)
        self.beat(who, ["counter", sid, old], f"{self.ref_(old)}은 안 된다고 하고, 대신 {self.slots[sid]['label']}: {text}"
                  f"(으)로 하자고 다시 제안{extra}", must)  # fmt: skip

    def agree(self, who, cid, how=None):
        how = self.rng.choice(OPEN_TONE) if how is None else how
        self.beat(who, ["agree", cid], f"{self.ref_(cid)}에 {how + ' ' if how else ''}동의")

    def object(self, who, cid, why=None):
        why = why or self.rng.choice(["학원", "알바", "가족 약속", "병원 예약", "시험 공부", "동아리"])
        self.beat(who, ["object", cid], f"{self.ref_(cid)}은 자기는 안 된다고 함 (이유: {why}). 다른 날짜나 시간은 말하지 않음")

    def hedge(self, who, cid):
        h = self.rng.choice(HEDGES)
        self.beat(who, ["hedge", cid], f"{self.ref_(cid)}에 '{h}'라고 답함 (이 말을 그대로). 동의가 아니라 결정을 미룬 말", [h])

    def declare(self, who, cid, summary=""):
        if self.rng.random() < 0.5:
            self.beat(who, ["declare", cid], f"{self.ref_(cid)}을 '확정'이라는 말로 결론 냄{summary}", ["확정"], word=True)
        else:
            self.beat(who, ["declare", cid], f"'그럼 그때 보자'처럼 {self.ref_(cid)}로 약속을 마무리하는 말을 함{summary}")  # fmt: skip

    def silent(self, names):
        if names:
            self.beat(None, ["silent", list(names)], f"{', '.join(names)}: 이 얘기에 끝까지 아무 말도 안 함 (이 사람들의 턴은 쓰지 않음)")

    def settle(self, cid, lead, pool=None, declare=None, summary=""):
        """Beats after which cid is settled: agreements one by one up to a majority, or one or two
        agreements and lead's declaration. pool = who may agree (default: everyone but lead)."""
        rng = self.rng
        pool = [p for p in (self.people if pool is None else pool) if p != lead]
        st = state(self.compiled(), len(self.beats), loose=False)[1].get(cid)  # who already agreed
        have = st["yes"] - {lead} if st else set()
        pool = [p for p in pool if p not in have and p not in (st["absent"] if st else set())]
        rng.shuffle(pool)
        short = need(len(self.people)) - 1 - len(have)
        if declare is None:
            declare = short > len(pool) or rng.random() < 0.35
        if declare:
            k = 0 if have else 1
            if k > len(pool):
                return False
            for p in pool[: rng.randint(k, max(k, min(2, len(pool))))]:
                self.agree(p, cid)
            if not have and not any(b["act"][:2] == ["agree", cid] for b in self.beats):
                return False
            self.declare(lead, cid, summary)
        else:
            for p in pool[: max(short, 0) + rng.randint(0, max(0, len(pool) - short))]:
                self.agree(p, cid)
        return True

    def compiled(self) -> dict:
        return {"participants": self.people, "slots": self.slots, "existing_events": self.existing, "beats": self.beats}


# ── replay: plan 8.3 applied to the acts ─────────────────────────────────────


def _ev(f: dict, status: str, attendees=(), absent=(), note=None) -> dict:
    d = dt.date.fromisoformat(f["date"])
    return {"title": f["title"], "date": f["date"], "weekday": WEEKDAYS[d.weekday()], "start": f["start"],
            "time_text": f["time_text"], "location": f["location"], "status": status, "attendees": list(attendees),
            "absent": list(absent), "note": note}  # fmt: skip


def state(sc: dict, upto: int, loose: bool) -> tuple[list[dict], dict]:
    """Gold events after the first `upto` beats, and the candidates (for builders)."""
    people, slots, existing = sc["participants"], sc["slots"], sc["existing_events"]
    n, cands, order = len(people), {}, []

    def new(key, kind, who, sid=None, i=None):
        absent = set(existing[i]["absent"]) if i is not None else set()
        loc = slots[sid]["location"] if sid else existing[i]["location"]
        cands[key] = {"kind": kind, "sid": sid, "i": i, "who": who, "yes": set(), "no": set(), "soft": set(),
                      "declared": False, "rejected": False, "absent": absent, "late": {}, "loc": loc, "pending": None}  # fmt: skip
        order.append(key)
        return cands[key]

    def target(key, who):
        if isinstance(key, str) and key.startswith("E"):
            i = int(key[1:])
            return cands.get(f"t{i}") or new(f"t{i}", "touch", who, i=i)
        return cands[key]

    instr = None
    for b in sc["beats"][:upto]:
        who, (act, *args) = b["who"], b["act"]
        if act == "propose":
            new(args[0], "new", who, sid=args[0])
        elif act == "counter":
            old = cands[args[1]]
            old["rejected"] = True
            if old["kind"] == "move":
                new(args[0], "move", who, sid=args[0], i=old["i"])
            else:
                new(args[0], "new", who, sid=args[0])
        elif act == "move":
            new(args[0], "move", who, sid=args[0], i=args[1])
        elif act == "cancel":
            new(f"x{args[0]}", "cancel", who, i=args[0])
        elif act in ("agree", "object", "absent", "back", "late", "declare", "drop", "place", "revive"):
            c = target(args[0], who)
            if act == "agree":
                c["yes"].add(who), c["no"].discard(who)
                if c["pending"] and c["pending"][1] != who:
                    c["loc"], c["pending"] = c["pending"][0], None
            elif act == "object":
                c["no"].add(who), c["yes"].discard(who)
            elif act == "absent":
                c["absent"].add(who), c["soft"].add(who), c["late"].pop(who, None)  # an earlier yes still counts
            elif act == "back":
                c["absent"].discard(who), c["soft"].discard(who), c["yes"].add(who)
            elif act == "late":
                c["late"][who] = args[1]
                c["soft"].add(who)
            elif act == "declare":
                assert who == c["who"], "only the proposer declares"
                c["declared"] = True
                if c["pending"]:
                    c["loc"], c["pending"] = c["pending"][0], None
            elif act == "drop":
                c["rejected"] = True
            elif act == "place":
                c["pending"] = (args[1], who)
            elif act == "revive":
                c.update(rejected=False, who=who, yes=set(), no=set(), declared=False)
                if len(args) > 1 and args[1]:
                    cands[args[1]]["rejected"] = True
        elif act == "instr":
            instr = (who, args[0])
        # info, leftover, silent, hedge: nothing changes

    if instr:
        return instruction_events(sc, *instr), cands

    def settled(c):
        if c["kind"] == "touch":
            return True
        yes = c["yes"] | {c["who"]} | (c["soft"] if loose else set())
        if c["rejected"] or c["no"]:
            return False
        return len(yes) >= need(n) or (c["declared"] and len(yes - {c["who"]}) >= 1)

    def fields(c):
        f = dict(slots[c["sid"]]) if c["sid"] else dict(existing[c["i"]])
        f["location"] = c["pending"][0] if loose and c["pending"] else c["loc"]
        return f

    def note(c):
        return ", ".join(f"{p} {m}분 늦게 합류" for p, m in c["late"].items()) or None

    def live(c, status):
        absent = sorted(c["absent"], key=people.index)
        return _ev(fields(c), status, [p for p in people if p not in c["absent"]], absent, note(c))

    out, done = [], set()
    for key in order:
        c = cands[key]
        if c["i"] is None:
            out.append(_ev(fields(c), "rejected") if c["rejected"] else live(c, "confirmed" if settled(c) else "proposed"))
            continue
        i = c["i"]
        if i in done:
            continue
        done.add(i)
        e, group = existing[i], [cands[k] for k in order if cands[k]["i"] == i]
        touch = next((g for g in group if g["kind"] == "touch"), None)
        base_absent = touch["absent"] if touch else set(e["absent"])
        base_late = touch["late"] if touch else {}
        moves = [g for g in group if g["kind"] == "move"]
        for g in moves:
            g["absent"] = g["absent"] | base_absent
            g["late"] = {**base_late, **g["late"]}
        won = [g for g in moves if settled(g)]
        if any(g["kind"] == "cancel" and settled(g) for g in group):
            out.append(_ev(e, "cancelled", e["attendees"], e["absent"], e["note"]))
            continue
        if won:
            w = won[-1]
            if (slots[w["sid"]]["date"], slots[w["sid"]]["start"]) != (e["date"], e["start"]):
                out.append(_ev(e, "rejected"))
            out += [_ev(fields(g), "rejected") for g in moves if g is not w]
            out.append(live(w, "confirmed"))
            continue
        absent = sorted(base_absent, key=people.index)
        touched = touch is not None or bool(moves) or any(g["kind"] == "cancel" for g in group)
        if touched:
            late = ", ".join(f"{p} {m}분 늦게 합류" for p, m in base_late.items()) or e["note"]
            out.append(_ev(e, "confirmed", [p for p in people if p not in base_absent], absent, late))
        out += [_ev(fields(g), "rejected") if g["rejected"] else live(g, "proposed") for g in moves]
    return out, cands


def instruction_events(sc: dict, who: str, op: dict) -> list[dict]:
    """[요청] mode (INSTRUCTION_MODE.md): apply the one instruction to the calendar entry."""
    people, existing = sc["participants"], sc["existing_events"]
    kind = op["op"]
    if kind == "none":
        return []
    if kind in ("add", "add_only"):
        s = sc["slots"][op["sid"]]
        going = [who if x == "나" else x for x in op.get("only", [])] or people
        return [_ev(s, "confirmed", [p for p in people if p in going], [])]
    e = existing[op.get("i", 0)]
    if kind == "cancel":
        return [_ev(e, "cancelled", e["attendees"], e["absent"], e["note"])]
    f = e | op.get("set", {})
    absent = list(e["absent"])
    late = e["note"]
    if kind in ("absent", "back", "late"):
        name = who if op["name"] == "나" else op["name"]
        if kind == "absent" and name not in absent:
            absent.append(name)
        if kind == "back" and name in absent:
            absent.remove(name)
        if kind == "late":
            late = f"{name} {op['minutes']}분 늦게 합류"
    absent = sorted(absent, key=people.index)
    out = [] if (f["date"], f["start"]) == (e["date"], e["start"]) else [_ev(e, "rejected")]
    return out + [_ev(f, "confirmed", [p for p in people if p not in absent], absent, late)]


def replay(sc: dict) -> tuple[list[list[dict]], list[bool]]:
    """Gold after each beat, and whether the two readings (absent/late as consent or not,
    a place before anyone agrees) disagree there."""
    golds, amb = [], []
    for k in range(1, len(sc["beats"]) + 1):
        strict, loose = state(sc, k, False)[0], state(sc, k, True)[0]
        golds.append(strict)
        amb.append(strict != loose)
    return golds, amb


# ── families ─────────────────────────────────────────────────────────────────
# Each returns (variant, intended final statuses). The statuses are a second, independent
# statement of the outcome: make() asserts replay() reaches them.


def _others(c, lead):
    return [p for p in c.people if p != lead]


def f_premature(c: Ctx):
    """Pattern 1: one or two yeses are not a confirmation; an objection or counter reopens it."""
    rng, p = c.rng, c.people
    lead, rest = p[0], p[1:]
    variant = rng.choices(["objection_counter", "open_counter", "late_objection", "objection_resolved", "hedge_mix"],
                          [30, 20, 15, 20, 15])[0]  # fmt: skip
    s1 = c.slot(place=rng.random() < 0.4)
    c.propose(lead, s1)
    first = rest[0]
    c.agree(first, s1, rng.choice(["'ㅇㅇ 됨'처럼 한두 글자로", "한두 글자로", "짧게"]))
    if variant in ("objection_counter", "open_counter"):
        objector = rest[1]
        s2 = c.slot(d=dt.date.fromisoformat(c.slots[s1]["date"]) if rng.random() < 0.5 else None, place=False,
                    title=c.slots[s1]["title"])  # fmt: skip
        if c.slots[s2]["start"] == c.slots[s1]["start"] and c.slots[s2]["date"] == c.slots[s1]["date"]:
            return None
        c.counter(objector, s2, s1)
        if variant == "open_counter":
            below = need(len(p)) - 2
            for q in rng.sample([x for x in p if x != objector], min(below, len(p) - 1)):
                c.agree(q, s2)
            c.silent([x for x in p if x != objector and not any(b["act"] == ["agree", s2] and b["who"] == x for b in c.beats)])
            return variant, ["rejected", "proposed"]
        if not c.settle(s2, objector):
            return None
        return variant, ["rejected", "confirmed"]
    if variant == "late_objection":
        if len(p) < 3:
            return None
        if not c.settle(s1, lead, pool=[x for x in rest if x != rest[-1]], declare=False):
            return None
        if state(c.compiled(), len(c.beats), False)[0][0]["status"] != "confirmed":
            return None
        c.object(rest[-1], s1)
        return variant, ["proposed"]
    if variant == "objection_resolved":
        objector = rest[1]
        c.object(objector, s1)
        c.beat(objector, ["agree", s1], f"다시 보니 일정 조정이 된다며 {c.ref_(s1)}에 동의로 바꿈")
        if not c.settle(s1, lead):
            return None
        return variant, ["confirmed"]
    hedgers = rest[1:3]
    for h in hedgers:
        c.hedge(h, s1)
    if rng.random() < 0.5:
        c.silent([x for x in rest if x not in hedgers and x != first])
        return variant + "_open", ["proposed"]
    for h in hedgers:
        c.beat(h, ["agree", s1], f"확인해 보니 된다며 {c.ref_(s1)}에 분명하게 동의")
    if state(c.compiled(), len(c.beats), False)[0][0]["status"] != "confirmed" and not c.settle(s1, lead):
        return None
    return variant + "_done", ["confirmed"]


def f_multi_slot(c: Ctx):
    """Pattern 2: several slots die on the way; only the last agreed one is confirmed."""
    rng, p = c.rng, c.people
    variant = rng.choices(["chain", "summary", "revive", "open_end", "same_day"], [25, 30, 15, 15, 15])[0]
    title = rng.choice(TITLES)
    d0 = c.day()
    s = [c.slot(title=title, d=d0 if variant == "same_day" else None, place=rng.random() < 0.3)]
    c.propose(p[0], s[0])
    owner = p[0]  # who proposed the idea now on the table: they don't reject their own idea
    for k in range(rng.choice([1, 2, 2, 3])):
        nxt = c.slot(title=title, d=d0 if variant == "same_day" else None)
        if any((c.slots[nxt]["date"], c.slots[nxt]["start"]) == (c.slots[x]["date"], c.slots[x]["start"]) for x in s):
            return None
        who = rng.choice(_others(c, owner))
        if rng.random() < 0.4:
            c.object(who, s[-1])
            who = rng.choice(_others(c, owner))
        c.counter(who, nxt, s[-1])
        owner = who
        s.append(nxt)
    last, lead = s[-1], c.beats[-1]["who"]
    if variant == "open_end":
        c.hedge(rng.choice(_others(c, lead)), last)
        return variant, ["rejected"] * (len(s) - 1) + ["proposed"]
    if variant == "revive":
        back = rng.choice(s[:-1])
        no = rng.choice(_others(c, lead))
        c.object(no, last)
        c.beat(no, ["revive", back, last], f"{c.ref_(last)}은 안 되니 아까 나왔던 {c.ref_(back)}이 차라리 낫다며 그걸로 다시 하자고 함")
        if not c.settle(back, no):
            return None
        return variant, ["confirmed" if x == back else "rejected" for x in s]
    if variant == "summary":
        dead = ", ".join(c.ref_(x) for x in s[:-1])
        c.agree(rng.choice(_others(c, lead)), last)
        c.beat(lead, ["declare", last], f"정리: {dead}은 안 되고 {c.ref_(last)}로 '확정'이라고 정리함 (안 되는 안도 이름을 말함)",
               ["확정"], word=True)  # fmt: skip
        if rng.random() < 0.5:
            x = c.slots[s[0]]
            phrase = x["say_time"] or x["say_date"]
            c.beat(rng.choice(p), ["info"], f"확정 뒤에 '{phrase}'는 진짜 안 됐다고 지나가듯 농담함. 결정을 바꾸는 말이 아님", [phrase])
        return variant, ["rejected"] * (len(s) - 1) + ["confirmed"]
    if not c.settle(last, lead):
        return None
    return variant, ["rejected"] * (len(s) - 1) + ["confirmed"]


def f_two_topics(c: Ctx):
    """Pattern 3: two events in one episode; each keeps its own status."""
    rng, p = c.rng, c.people
    variant = rng.choices(["open", "rejected", "both", "reversed", "same_day"], [30, 20, 20, 15, 15])[0]
    t1, t2 = rng.sample(TITLES, 2)
    a = c.slot(title=t1, say_title=True, place=rng.random() < 0.3)
    b = c.slot(title=t2, say_title=True, d=dt.date.fromisoformat(c.slots[a]["date"]) if variant == "same_day" else None)
    if c.slots[a]["start"] == c.slots[b]["start"] and c.slots[a]["date"] == c.slots[b]["date"]:
        return None
    la, lb = p[0], p[1]
    first, second = (b, a) if variant == "reversed" else (a, b)
    c.propose(la, first)
    if variant == "reversed":  # the first topic stays open, the second is settled
        if need(len(p)) > 2:  # one yes stays below a majority
            c.agree(rng.choice(_others(c, la)), first)
        else:
            c.hedge(rng.choice(_others(c, la)), first)
        c.beat(lb, ["propose", second], f"{c.slots[second]['label']}: 그건 그렇고 별개로 {c.pitch(second)[0]} 제안",
               c.pitch(second)[1])  # fmt: skip
        if not c.settle(second, lb, pool=[x for x in p if x != la]):
            return None
        return variant, ["proposed", "confirmed"]
    if not c.settle(first, la):
        return None
    c.beat(lb, ["propose", second], f"{c.slots[second]['label']}: 이어서 별개의 일정으로 {c.pitch(second)[0]} 제안",
           c.pitch(second)[1])  # fmt: skip
    if variant in ("open", "same_day"):
        if need(len(p)) > 2 and rng.random() < 0.6:
            c.agree(rng.choice(_others(c, lb)), second)
        else:
            c.hedge(rng.choice(_others(c, lb)), second)
        return variant, ["confirmed", "proposed"]
    if variant == "rejected":
        for q in rng.sample(_others(c, lb), min(2, len(p) - 1)):
            c.object(q, second)
        c.beat(lb, ["drop", second], f"그럼 {c.ref_(second)}은 없던 걸로 하자고 함. 다른 날짜는 정하지 않음")
        return variant, ["confirmed", "rejected"]
    if not c.settle(second, lb):
        return None
    return variant, ["confirmed", "confirmed"]


def f_attendance(c: Ctx):
    """Pattern 4: absentees, per-event attendance, silent members, people who come back."""
    rng, p = c.rng, c.people
    if len(p) < 3:
        return None
    variant = rng.choices(["absent", "two_events", "silent", "back", "late"], [35, 20, 15, 15, 15])[0]
    lead, rest = p[0], p[1:]
    s1 = c.slot(place=rng.random() < 0.4)
    c.propose(lead, s1)
    x = rng.choice(rest)
    going = [q for q in rest if q != x]
    if variant in ("absent", "two_events", "back"):
        n_going = len(going)
        c.beat(x, ["absent", s1], f"{c.ref_(s1)}에 자기는 못 간다고 하면서 " + rng.choice(
            ["나머지끼리 하라고 함", f"'{'너네 ' + str(n_going + 1) + '명이서' if n_going < 5 else '너네끼리'} 해'라고 함", "자기 빼고 진행하라고 함"]))  # fmt: skip
        if variant == "back":
            c.agree(going[0], s1)
            c.beat(x, ["back", s1], f"사정이 바뀌어서 {c.ref_(s1)}에 자기도 갈 수 있다고 다시 말함")
        if not c.settle(s1, lead, pool=going, declare=False if need(len(p)) - 1 <= len(going) else None):
            return None
        if variant == "two_events":
            s2 = c.slot(title=rng.choice([t for t in TITLES if t != c.slots[s1]["title"]]), say_title=True)
            lb = rng.choice(going)
            c.beat(lb, ["propose", s2], f"{c.slots[s2]['label']}: 별개로 {c.pitch(s2)[0]} 제안", c.pitch(s2)[1])
            c.beat(x, ["agree", s2], f"{c.ref_(s2)}은 자기도 갈 수 있다고 분명히 동의 (앞 일정만 못 감)")
            if not c.settle(s2, lb):
                return None
            return variant, ["confirmed", "confirmed"]
        return variant, ["confirmed"]
    if variant == "late":
        m = rng.choice([10, 15, 20, 30, 40])
        c.beat(x, ["late", s1, m], f"{c.ref_(s1)}에 가긴 가는데 '{m}분' 늦는다고 함", [f"{m}분"])
        if not c.settle(s1, lead, pool=going, declare=False if need(len(p)) - 1 <= len(going) else None):
            return None
        return variant, ["confirmed"]
    k = need(len(p)) - 1
    for q in rng.sample(rest, k):
        c.agree(q, s1)
    c.silent([q for q in rest if not any(b["act"] == ["agree", s1] and b["who"] == q for b in c.beats)])
    return variant, ["confirmed"]


def _p5_time(c: Ctx, variant: str):
    """(minutes or None, time_text, say_time, context phrase) for the AM/PM family."""
    rng = c.rng
    if variant == "evening_word":
        h, m = rng.choice([20, 20, 21, 21, 22, 23]), rng.choice([0, 0, 30])
        word = rng.choice(["밤", "저녁", "오후"] if h < 22 else ["밤"])
        return h * 60 + m, None, f"{word} {h - 12}시" + (" 반" if m else ""), None
    if variant == "bare_ctx":
        h, m = rng.choice([8, 9, 9, 10]), rng.choice([0, 0, 30])
        ctx = rng.choice(["저녁 먹고", "저녁에", "밤에"])
        return (h + 12) * 60 + m, None, f"{h}시" + (" 반" if m else ""), ctx
    if variant == "morning":
        h, m = rng.choice([9, 10, 10, 11]), rng.choice([0, 30])
        if rng.random() < 0.5:
            return h * 60 + m, None, rng.choice(["아침 ", "오전 "]) + f"{h}시" + (" 반" if m else ""), None
        return h * 60 + m, None, f"{h}시" + (" 반" if m else ""), rng.choice(["아침에", "오전에"])
    h, m = rng.choice([8, 9, 10]), rng.choice([0, 0, 30])  # bare_none
    said = f"{h}시" + (" 반" if m else "")
    return None, said, said, None


def f_times(c: Ctx):
    """Pattern 5: evening words give 24-hour times; a bare 8~11시 needs context, else time_text."""
    rng, p = c.rng, c.people
    variant = rng.choices(["evening_word", "bare_ctx", "bare_none", "shift_ctx", "morning", "deadline"],
                          [30, 20, 15, 15, 10, 10])[0]  # fmt: skip
    lead = p[0]
    if variant == "shift_ctx":  # "7시" (19:00) then "8시로 미루자": the bare 8 is next to a known evening time
        s1 = c.slot(minutes=19 * 60, say_time=rng.choice(["7시", "저녁 7시"]))
        c.propose(lead, s1)
        h = rng.choice([8, 9])
        s2 = c.slot(d=dt.date.fromisoformat(c.slots[s1]["date"]), minutes=(h + 12) * 60, say_time=f"{h}시",
                    title=c.slots[s1]["title"])  # fmt: skip
        who = p[1]
        text, must = c.pitch(s2, title=False)
        c.beat(who, ["counter", s2, s1], f"{c.ref_(s1)}은 조금 이르다며 같은 날 {c.slots[s2]['label']}: {text}(으)로 "
               f"{rng.choice(['미루자', '늦추자'])}고 함 (저녁/밤 같은 말은 붙이지 않음)", must)  # fmt: skip
        if not c.settle(s2, who):
            return None
        return variant, ["rejected", "confirmed"]
    if variant == "deadline":
        s1 = c.slot()
        t = rng.choice([x for x in ("6시", "18시", "5시", "11시 59분") if times_in(x, c.ref) != {c.slots[s1]["start"]}])
        phrase = rng.choice(EXTERNAL).format(t=t)
        c.beat(rng.choice(p), ["info"], f"'{phrase}'라고 과제/시험 얘기를 함 (이 말을 그대로). 팀 모임 시간이 아님", [phrase])
        c.propose(lead, s1)
        if not c.settle(s1, lead):
            return None
        return variant, ["confirmed"]
    minutes, time_text, say_time, ctx = _p5_time(c, variant)
    s1 = c.slot(minutes=minutes, time_text=time_text, say_time=say_time)
    text, must = c.pitch(s1)
    if ctx:
        c.beat(lead, ["propose", s1], f"{c.slots[s1]['label']}: {text} 제안. 같은 메시지에 '{ctx}'라는 말을 꼭 붙임", must + [ctx])
    elif variant == "bare_none":
        c.bare_hour = True
        c.beat(lead, ["propose", s1], f"{c.slots[s1]['label']}: {text} 제안. 아침/저녁/오전/오후/밤 같은 말은 어디에도 쓰지 않음", must)
    else:
        c.beat(lead, ["propose", s1], f"{c.slots[s1]['label']}: {text} 제안", must)
    if rng.random() < 0.35 and len(p) >= 3:
        c.hedge(p[1], s1)
        c.silent(p[2:])
        return variant + "_open", ["proposed"]
    if not c.settle(s1, lead):
        return None
    return variant, ["confirmed"]


def f_after_accept(c: Ctx):
    """Pattern 6: the event is already on the calendar; the episode changes it, cancels it,
    adds another one, or only talks around it."""
    rng, p = c.rng, c.people
    variant = rng.choices(["time", "date", "cancel", "add_other", "logistics", "deadline", "absent", "refused"],
                          [18, 10, 14, 14, 14, 8, 12, 10])[0]  # fmt: skip
    i = c.calendar(place=rng.choice([None, None, rng.choice(PLACES)]))
    e, say = c.existing[i], c.say(i)
    lead = rng.choice(p)
    name = f"'{say['date']}' '{say['title']}'"
    if variant in ("time", "refused"):
        old = int(e["start"][:2]) * 60 + int(e["start"][3:])
        new = old + rng.choice([-120, -60, -30, 30, 60, 90])
        if not 11 * 60 <= new <= 21 * 60:
            return None
        sid = c.moved(i, minutes=new)
        c.beat(lead, ["move", sid, i], f"이미 캘린더에 있는 {name} 일정을 시간만 '{c.slots[sid]['say_time']}'(으)로 바꾸자고 함",
               [say["date"], say["title"], c.slots[sid]["say_time"]])  # fmt: skip
        if variant == "refused":
            for q in rng.sample(_others(c, lead), min(2, len(p) - 1)):
                c.object(q, sid)
            c.beat(lead, ["drop", sid], "알겠다며 그냥 원래 시간대로 하자고 함")
            return variant, ["confirmed", "rejected"]
        if not c.settle(sid, lead):
            return None
        return variant, ["rejected", "confirmed"]
    if variant == "date":
        sid = c.moved(i, d=c.day())
        c.beat(lead, ["move", sid, i], f"이미 캘린더에 있는 {name} 일정을 시간은 그대로 두고 날짜만 '{c.slots[sid]['say_date']}'(으)로 옮기자고 함",
               [say["date"], say["title"], c.slots[sid]["say_date"]])  # fmt: skip
        if not c.settle(sid, lead):
            return None
        return variant, ["rejected", "confirmed"]
    if variant == "cancel":
        c.beat(lead, ["cancel", i], f"이미 캘린더에 있는 {name} 일정을 사정이 생겨서 취소하자고 함", [say["date"], say["title"]])
        if not c.settle(f"x{i}", lead):
            return None
        return variant, ["cancelled"]
    if variant == "absent":
        x = rng.choice(p)
        c.beat(x, ["absent", f"E{i}"], f"캘린더에 있는 {name} 일정에 자기는 못 간다고 함. 일정은 그대로 하라고 함", [say["date"], say["title"]])
        if rng.random() < 0.6:
            q = rng.choice([y for y in p if y != x])
            c.beat(q, ["info"], "알겠다고 짧게 답함 (일정을 바꾸자는 말이 아님)")
        return variant, ["confirmed"]
    if variant == "add_other":
        s = c.slot(title=rng.choice([t for t in TITLES if t != e["title"]]), say_title=True)
        c.beat(rng.choice(p), ["info"], f"캘린더에 있는 {name} 얘기를 잠깐 함 (그대로 한다는 내용, 바꾸자는 말 없음)", [say["title"]])
        c.propose(lead, s)
        if not c.settle(s, lead):
            return None
        return variant, ["confirmed"]
    if variant == "deadline":
        t = rng.choice(["6시", "18시", "5시", "11시 59분"])
        phrase = rng.choice(EXTERNAL).format(t=t)
        c.beat(lead, ["info"], f"'{phrase}'라고 과제/시험 얘기를 함 (이 말을 그대로). 팀 모임 시간이 아님", [phrase])
        c.beat(rng.choice(_others(c, lead)), ["info"], "그거 하고 나서 캘린더에 있는 일정에 가면 되겠다고 함. 일정을 바꾸지 않음. 날짜나 시간은 말하지 않음")
        return variant, []
    times = [say["time"]] if say["time"] else []
    lines = [f"{name} 일정 시간이 '{say['time'] or '언제'}'인지 확인하는 질문", "맞다고 확인해 줌", "늦지 말자고 하거나 준비물 얘기를 함"]
    c.beat(lead, ["info"], lines[0] + " (이미 정해진 일정, 바꾸자는 말 아님)", [say["date"], say["title"], *times])
    c.beat(rng.choice(_others(c, lead)), ["info"], lines[1])
    c.beat(rng.choice(p), ["info"], lines[2])
    return "logistics", []


def f_leftover(c: Ctx):
    """Pattern 7: 1-3 closing lines of an already handled topic, then a new topic."""
    rng, p = c.rng, c.people
    kind = rng.choice(list(LEFTOVER))
    if kind == "accepted":
        i = c.calendar()
        say = c.say(i)
        c.beat(rng.choice(p), ["leftover"], f"(앞에서 이미 끝난 얘기) 캘린더에 있는 '{say['title']}' 일정 카드를 수락했다는 식의 말: "
               f"'{rng.choice(LEFTOVER[kind])}'", [say["title"]])  # fmt: skip
    elif kind == "cancelled":
        d = c.day()
        said = _say_date(c.ref, d, rng)
        c.extra_dates.append(d.isoformat())
        c.beat(rng.choice(p), ["leftover"], f"(앞에서 이미 끝난 얘기) '{said}' 모임이 취소된 걸 확인하는 말: "
               f"'{rng.choice(LEFTOVER[kind])}' (취소는 이미 처리됨)", [said])  # fmt: skip
    else:
        c.beat(rng.choice(p), ["leftover"], f"(앞에서 결론 없이 끝난 얘기) '{rng.choice(LEFTOVER[kind])}'라고 말함")
    if rng.random() < 0.5:
        c.beat(rng.choice(p), ["leftover"], "앞 얘기에 'ㅇㅇ'나 'ㅋㅋ' 정도로 짧게 반응")
    lead = rng.choice(p)
    s = c.slot(place=rng.random() < 0.3)
    c.beat(lead, ["propose", s], f"화제를 바꿔서 {c.slots[s]['label']}: {c.pitch(s)[0]} 제안", c.pitch(s)[1])
    if rng.random() < 0.3:
        c.hedge(rng.choice(_others(c, lead)), s)
        return kind + "_open", ["proposed"]
    if not c.settle(s, lead):
        return None
    return kind, ["confirmed"]


def f_late_details(c: Ctx):
    """Pattern 8: the time is settled first; place / an absentee / a late joiner come after."""
    rng, p = c.rng, c.people
    variant = rng.choices(["place", "absent", "both", "late"], [35, 25, 25, 15])[0]
    lead = p[0]
    s = c.slot(place=False)
    c.propose(lead, s)
    if not c.settle(s, lead):
        return None
    if variant in ("place", "both"):
        place = rng.choice(PLACES)
        sayer = rng.choice(p)
        c.beat(sayer, ["place", s, place], f"장소는 '{place}'(으)로 하자고 함", [place])
        c.agree(rng.choice([q for q in p if q != sayer]), s, "장소에 짧게")
    if variant in ("absent", "both") and len(p) >= 3:
        x = rng.choice(p[1:])
        c.beat(x, ["absent", s], f"뒤늦게 자기는 {c.ref_(s)}에 못 가게 됐다고 함. 나머지끼리 하라고 함")
    if variant == "late":
        m = rng.choice([10, 20, 30])
        c.beat(rng.choice(p[1:]), ["late", s, m], f"자기는 '{m}분' 늦게 간다고 함", [f"{m}분"])
    return variant, ["confirmed"]


def f_short(c: Ctx):
    """Pattern 9: 1-4 messages."""
    rng, p = c.rng, c.people
    n = len(p)
    variant = {2: "pair", 3: rng.choice(["all_ok", "no_reply", "refuse", "pair_cancel"]),
               4: rng.choice(["one_of_four", "no_reply", "refuse", "pair_cancel"])}.get(n, rng.choice(["no_reply", "all_ok"]))  # fmt: skip
    if variant == "pair_cancel":
        i = c.calendar()
        say = c.say(i)
        c.beat(p[0], ["cancel", i], f"'{say['date']}' '{say['title']}' 취소하자고 아주 짧게 말함", [say["date"], say["title"]])
        c.agree(p[1], f"x{i}", "한두 글자로")
        return variant, ["cancelled" if need(n) <= 2 else "confirmed"]
    s = c.slot(say_title=rng.random() < 0.4, untimed=rng.random() < 0.15)
    c.propose(p[0], s, ". 아주 짧게")
    if variant == "no_reply":
        return variant, ["proposed"]
    if variant == "refuse":
        c.object(p[1], s, "짧게 아무 이유나")
        return variant, ["proposed"]
    if variant == "one_of_four":
        c.agree(p[1], s, "'ㅇㅋ' 한 마디로")
        return variant, ["proposed"]
    for q in p[1 : need(n) + (1 if rng.random() < 0.5 else 0)]:
        c.agree(q, s, "한두 글자로")
    return variant, ["confirmed"]


INSTR_OPS = {"time": 14, "shift": 7, "date": 8, "datetime": 5, "place": 10, "absent": 10, "back": 4, "late": 5,
             "cancel": 10, "add": 15, "add_only": 3, "combo": 5, "none": 6, "vague_add": 3, "no_target": 3,
             "rename": 2}  # fmt: skip


def _bare(h: int, near: int) -> tuple[int, bool]:
    """A bare 8~11시 said about an event at `near` (minutes): the reading closest to it.
    Unclear (both readings 4h+ away, or equally far) → (h, False)."""
    a, b = h * 60, (h + 12) * 60
    da, db = abs(a - near), abs(b - near)
    if min(da, db) > 3 * 60 or da == db:
        return h * 60, False
    return (a if da < db else b), True


def f_instruction(c: Ctx):
    """Pattern 10: one '[요청] ...' turn, applied to the event it refers to."""
    rng, p = c.rng, c.people
    op = rng.choices(list(INSTR_OPS), list(INSTR_OPS.values()))[0]
    who = rng.choice(p)
    lead = f"'[요청] '으로 시작하는 메시지 한 줄만 씀. 채팅창에 봇에게 시키듯 짧게."
    if op in ("add", "add_only", "vague_add", "none"):
        for _ in range(rng.choice([0, 0, 1])):
            c.calendar()
        if op == "none":
            what = rng.choice(["회의록 정리해 달라는 부탁", "고맙다는 말", "발표 자료 파일 어디 있냐는 질문", "봇이 잘 되는지 시험하는 말",
                               "다음에 다 같이 밥 먹자는 막연한 말(날짜 없음)"])  # fmt: skip
            c.beat(who, ["instr", {"op": "none"}], f"{lead} 내용: {what}. 날짜나 시간은 쓰지 않음", ["[요청]"])
            return op, []
        if op == "vague_add":
            s = c.slot()
            c.beat(who, ["instr", {"op": "none"}], f"{lead} 내용: '{c.slots[s]['title']}' 잡아 달라는데 날짜도 시간도 말하지 않음",
                   ["[요청]", c.slots[s]["title"]])  # fmt: skip
            del c.slots[s]
            return op, []
        r = rng.random()  # 10% no time, 10% a word time ("점심시간" → time_text)
        s = c.slot(place=rng.random() < 0.5, say_title=True, untimed=r < 0.1,
                   time_text=rng.choice(NON_NUMERIC) if 0.1 <= r < 0.2 else None)
        text, must = c.pitch(s)
        only = []
        if op == "add_only":
            only = ["나", rng.choice([x for x in p if x != who])]
            text += f". 참석은 '나랑 {only[1]}'만이라고 함"
            must.append(only[1])
        c.beat(who, ["instr", {"op": op, "sid": s, "only": only}], f"{lead} 내용: {text} 일정을 새로 잡아 달라는 요청",
               ["[요청]", *must])  # fmt: skip
        return op, ["confirmed"]
    i = c.calendar(place=rng.choice([None, rng.choice(PLACES)]), absent=rng.sample(p[1:], 1) if (op == "back" or rng.random() < 0.15) and len(p) > 2 else ())
    e = c.existing[i]
    start = int(e["start"][:2]) * 60 + int(e["start"][3:])
    if op == "no_target":  # a change with no event sent: nothing to change
        c.existing.clear()
        c.beat(who, ["instr", {"op": "none"}], f"{lead} 내용: 시간을 '{_say_clock(17, 0, rng)}'(으)로 바꿔 달라는 요청. 무슨 일정인지는 말하지 않음",
               ["[요청]"])  # fmt: skip
        return op, []
    st, words = {}, []
    if op in ("time", "combo", "datetime"):
        while True:
            new = rng.randrange(10 * 60, 22 * 60 + 1, 30)
            if new != start:
                break
        h = new // 60
        if 8 <= h % 12 <= 11 and h != 12 and rng.random() < 0.6:  # a bare 8~11시: take it from the event's time
            got, clear = _bare(h % 12 if h < 12 else h - 12, start)
            if not clear:
                return None
            new, say = got + new % 60, f"{(h % 12)}시" + (" 반" if new % 60 else "")
            if new == start:
                return None
        else:
            say = _say_clock(h, new % 60, rng)
        st["start"], words = _hm(new), [say]
    if op == "shift":
        step = rng.choice([30, 60, -30, -60, 90])
        new = start + step
        if not 10 * 60 <= new <= 22 * 60:
            return None
        st["start"] = _hm(new)
        amount = {30: "30분", 60: rng.choice(["1시간", "한 시간"]), 90: "1시간 반"}[abs(step)]
        words = [amount, "미뤄" if step > 0 else "당겨"]
    if op in ("date", "datetime"):
        d = c.day()
        said = _say_date(c.ref, d, rng)
        st["date"], st["weekday"] = d.isoformat(), WEEKDAYS[d.weekday()]
        words = [said] + words
    if op in ("place", "combo"):
        place = rng.choice([x for x in PLACES if x != e["location"]])
        st["location"] = place
        words.append(place)
    if op == "rename":
        title = rng.choice([t for t in TITLES if t != e["title"]])
        st["title"] = title
        words.append(title)
    info = {"op": op if op in ("absent", "back", "late", "cancel") else "change", "i": i, "set": st}
    if op == "absent":
        name = rng.choice(["나", *[x for x in p if x != who and x not in e["absent"]]])
        info["name"] = name
        words = [] if name == "나" else [name]
        desc = "자기가 못 간다는" if name == "나" else f"'{name}'이(가) 못 온다는"
    elif op == "back":
        if not e["absent"]:
            return None
        info["name"] = e["absent"][0]
        words = [e["absent"][0]]
        desc = f"'{e['absent'][0]}'도 올 수 있게 됐다는"
    elif op == "late":
        name = rng.choice(["나", *[x for x in p if x != who and x not in e["absent"]]])
        info["name"], info["minutes"] = name, rng.choice([10, 20, 30])
        words = [f"{info['minutes']}분"] + ([] if name == "나" else [name])
        desc = f"{'자기가' if name == '나' else repr(name) + '이(가)'} '{info['minutes']}분' 늦는다는"
    elif op == "cancel":
        desc = "이 일정을 취소해 달라는"
    elif op == "shift":
        desc = f"시간을 '{words[0]}' '{words[1]}' 달라는"
    else:
        desc = " / ".join(f"'{w}'" for w in words) + " (으)로 바꿔 달라는"
    refer = rng.random() < 0.4
    say = c.say(i)
    if refer:
        words.append(say["title"])
    tail = f" 일정 이름 '{say['title']}'을 같이 써도 됨." if refer else " 무슨 일정인지(이름, 날짜)는 말하지 않음 (지금 보고 있는 일정에 대한 요청)."
    c.beat(who, ["instr", info], f"{lead} 내용: {desc} 요청.{tail} 다른 날짜나 시간은 쓰지 않음", ["[요청]", *words])
    want = {"cancel": ["cancelled"]}.get(op) or (["rejected", "confirmed"] if {"date", "start"} & set(st) else ["confirmed"])
    return op, want


FAMILIES = {  # name → (builder, count in a 2,000 run, team sizes and weights, prefix cuts at most)
    "premature": (f_premature, 320, ([4, 5, 6], [5, 3, 2]), 2),  # in a team of 3 one yes is a majority
    "multi_slot": (f_multi_slot, 240, ([3, 4, 5, 6], [3, 4, 2, 1]), 2),
    "two_topics": (f_two_topics, 200, ([2, 3, 4, 5, 6], [1, 3, 4, 2, 1]), 2),
    "attendance": (f_attendance, 200, ([3, 4, 5, 6], [2, 4, 3, 2]), 1),
    "times": (f_times, 150, ([2, 3, 4, 5], [1, 3, 3, 2]), 1),
    "after_accept": (f_after_accept, 200, ([2, 3, 4, 5, 6], [1, 3, 4, 2, 1]), 1),
    "leftover": (f_leftover, 140, ([2, 3, 4, 5], [1, 3, 4, 2]), 1),
    "late_details": (f_late_details, 140, ([3, 4, 5, 6], [3, 4, 2, 1]), 2),
    "short": (f_short, 130, ([2, 3, 4, 5], [3, 3, 3, 1]), 1),
    "instruction": (f_instruction, 280, ([2, 3, 4, 5, 6], [1, 3, 4, 2, 1]), 0),
}  # fmt: skip


def make(rng: random.Random, family: str) -> dict:
    """One v5 scenario of `family`, with the gold after every beat."""
    build, _, (sizes, weights), cuts = FAMILIES[family]
    while True:
        ref = dt.date(2026, 3, 1) + dt.timedelta(days=rng.randint(0, 305))
        people = rng.sample(NAMES, rng.choices(sizes, weights)[0])
        c = Ctx(rng, ref, people)
        got = build(c)
        if not got:
            continue
        variant, intent = got
        sc = c.compiled() | {
            "type": family, "variant": variant, "ref_date": ref.isoformat(), "says_confirm": c.says_confirm,
            "bare_hour": c.bare_hour, "extra_dates": c.extra_dates, "max_cuts": cuts,
            "setting": rng.choice(SETTINGS), "style": rng.choice(STYLES), "agree_words": rng.sample(AGREE, 4),
            "small_talk": rng.choice(SMALL_TALK) if family not in ("short", "instruction") else None,
        }  # fmt: skip
        unnamed(sc)
        golds, amb = replay(sc)
        if amb[-1]:  # the outcome hangs on whether "못 가, 너네끼리 해" counts as a yes: build another
            continue
        assert [e["status"] for e in golds[-1]] == intent, (family, variant, intent, golds[-1], sc["beats"])
        speaking = sum(1 for b in sc["beats"] if b["who"])
        sc |= {"golds": golds, "ambiguous": amb, "events": golds[-1],
               "min_turns": speaking, "max_turns": 1 if family == "instruction" else 2 * speaking + 6,
               "turns": speaking if family in ("short", "instruction") else rng.randint(speaking, speaking + 4),
               "gaps": [rng.randint(0, 4) for _ in range(60)]}  # fmt: skip
        if _fit_clock(sc, rng):
            return sc


def unnamed(sc: dict) -> None:
    """A meeting whose name nobody says is "모임" (as in v4), never a made-up title."""
    said = "".join(w for b in sc["beats"] for w in b["must"]).replace(" ", "")
    for s in sc["slots"].values():
        if s["title"].replace(" ", "") not in said:
            s["title"] = "모임"


def _fit_clock(sc: dict, rng) -> bool:
    """start_minute so the chat stays on ref_date and ends 30 min before any event that day."""
    span = sum(sc["gaps"][: sc["max_turns"]])
    hi = 24 * 60 - 1 - span
    for e in list(sc["slots"].values()) + sc["existing_events"]:
        if e["date"] == sc["ref_date"] and e["start"]:
            hi = min(hi, int(e["start"][:2]) * 60 + int(e["start"][3:]) - 30 - span)
    if hi < 7 * 60:
        return False
    sc["start_minute"] = rng.randint(min(8 * 60, hi), min(hi, 23 * 60))
    return True


def errors(sc: dict) -> list[str]:
    """What is wrong with a scenario's gold, re-derived from the acts and the words."""
    errs = []
    ref = dt.date.fromisoformat(sc["ref_date"])
    golds, amb = replay(sc)
    if golds != sc["golds"] or amb != sc["ambiguous"]:
        errs.append("stored golds are not replay() of the acts")
    people = sc["participants"]
    for k, events in enumerate(golds):
        for e in events:
            d = dt.date.fromisoformat(e["date"])
            if d < ref or e["weekday"] != WEEKDAYS[d.weekday()]:
                errs.append(f"beat {k + 1}: date/weekday {e['date']} {e['weekday']}")
            if e["start"] and e["time_text"]:
                errs.append("both start and time_text")
            if set(e["attendees"]) & set(e["absent"]) or set(e["attendees"] + e["absent"]) - set(people):
                errs.append("attendees")
            if e["status"] == "rejected" and e["attendees"]:
                errs.append("rejected event has attendees")
    slot_dates = {s["date"] for s in sc["slots"].values()} | {e["date"] for e in sc["existing_events"]} | set(sc["extra_dates"])
    slot_dates |= {e["date"] for events in golds for e in events}
    for b in sc["beats"]:
        for w in b["must"]:
            for m in parse(f"{w} 회의", ref):
                if m.date and not m.tentative and m.date.isoformat() not in slot_dates:
                    errs.append(f"must '{w}' names a date that is no slot")
    for sid, s in sc["slots"].items():
        if s["say_date"] and _date_of(s["say_date"], ref) != dt.date.fromisoformat(s["date"]):
            errs.append(f"{sid} date phrase '{s['say_date']}' doesn't resolve to {s['date']}")
        if s["start"] and s["say_time"]:
            t = times_in(s["say_time"], ref)
            if t and t != {s["start"]}:
                errs.append(f"{sid} time phrase '{s['say_time']}' reads as {t}, not {s['start']}")
    said = "".join(w for b in sc["beats"] for w in b["must"]).replace(" ", "")
    if any(e["title"] != "모임" and e["title"].replace(" ", "") not in said for g in golds for e in g
           if e["title"] not in {x["title"] for x in sc["existing_events"]}):  # fmt: skip
        errs.append("a gold title nobody says")
    for k, b in enumerate(sc["beats"]):  # a side remark must not say the time of an idea proposed later
        later = {sc["slots"][x["act"][1]]["start"] for x in sc["beats"][k + 1 :] if x["act"][0] in ("propose", "counter", "move")}
        if b["act"][0] in ("info", "leftover") and any(times_in(w, ref) & later for w in b["must"]):
            errs.append("a side remark says the time of a later idea")
    if not sc["says_confirm"] and any("확정" in b["say"] for b in sc["beats"]):
        errs.append("확정 in beats")
    return errs
