import datetime as dt
import time

from app.config import settings
from app.db.models import EventStatus
from app.schedule import tracker
from app.schemas import ScheduleEvent

THU = (dt.date.today() + dt.timedelta(days=3)).isoformat()


def _setup(client, login, ai=True):
    owner, member = login("o@x.com"), login("m@x.com")
    ws = client.post(
        "/workspaces", json={"name": "팀", "ai_schedule_on": ai}, headers=owner
    ).json()
    client.post(f"/invites/{ws['invite_code']}", headers=member)
    ch = client.get(f"/workspaces/{ws['id']}/channels", headers=owner).json()[0]
    return ws, ch, owner, member


def _ws_url(ch, headers):
    return f"/ws/channels/{ch['id']}?token={headers['Authorization'].split()[1]}"


def test_messages_moderation_and_websocket(client, login):
    _ws, ch, owner, member = _setup(client, login, ai=False)
    with client.websocket_connect(_ws_url(ch, member)) as sock:
        r = client.post(
            f"/channels/{ch['id']}/messages",
            json={"text": "씨@발 늦었다"},
            headers=owner,
        )
        assert r.status_code == 201 and r.json()["is_profane"]
        pushed = sock.receive_json()
        assert pushed["type"] == "message" and pushed["data"]["id"] == r.json()["id"]

    for i in range(3):
        client.post(
            f"/channels/{ch['id']}/messages", json={"text": f"m{i}"}, headers=owner
        )
    page = client.get(f"/channels/{ch['id']}/messages?limit=2", headers=member).json()
    assert [m["text"] for m in page] == ["m1", "m2"]
    older = client.get(
        f"/channels/{ch['id']}/messages?before_id={page[0]['id']}", headers=member
    ).json()
    assert [m["text"] for m in older] == ["씨@발 늦었다", "m0"]

    assert (
        client.post(
            "/reports",
            json={"message_id": page[0]["id"], "type": "missed"},
            headers=member,
        ).status_code
        == 201
    )
    outsider = login("z@x.com")
    assert (
        client.get(f"/channels/{ch['id']}/messages", headers=outsider).status_code
        == 404
    )
    assert (
        client.post(
            "/reports",
            json={"message_id": page[0]["id"], "type": "missed"},
            headers=outsider,
        ).status_code
        == 404
    )


def test_schedule_card_flow(client, login, monkeypatch):
    monkeypatch.setattr(settings, "schedule_debounce_seconds", 0)
    meeting = ScheduleEvent(
        title="팀 회의",
        date=THU,
        start="19:00",
        status="confirmed",
        attendees=["o", "m"],
    )
    result, seen = [meeting], []

    async def fake_track(req):
        seen.append(req)
        return result

    monkeypatch.setattr(tracker, "track", fake_track)
    ws, ch, owner, member = _setup(client, login)
    cid = ch["id"]

    def say(text):
        client.post(f"/channels/{cid}/messages", json={"text": text}, headers=owner)
        assert sock.receive_json()["type"] == "message"

    def pending():
        return client.get(f"/channels/{cid}/suggestions", headers=member).json()

    with client.websocket_connect(_ws_url(ch, member)) as sock:
        say("ㅇㅋ 목 7시 확정")
        card = sock.receive_json()
        assert card["type"] == "suggestion" and card["data"]["payload"]["kind"] == "add"
        assert seen[0].participants == ["o", "m"]
        assert seen[0].dialogue[-1].text == "ㅇㅋ 목 7시 확정"
        assert [c["id"] for c in pending()] == [card["data"]["id"]]

        sid = card["data"]["id"]
        r = client.post(
            f"/suggestions/{sid}/action", json={"action": "added"}, headers=member
        )
        assert (
            r.json()["event"]["source"] == "ai"
            and r.json()["event"]["start"] == "19:00:00"
        )
        assert sock.receive_json()["type"] == "suggestion_resolved"
        assert pending() == []
        again = client.post(
            f"/suggestions/{sid}/action", json={"action": "added"}, headers=member
        )
        assert again.status_code == 409

        result[0] = meeting.model_copy(update={"status": EventStatus.cancelled})
        say("목 회의 취소하자")
        card = sock.receive_json()
        assert card["data"]["payload"]["kind"] == "cancel"

        # Same conclusion on the next run → no duplicate card.
        say("ㅇㅇ 취소")
        while len(seen) < 3:
            time.sleep(0.01)
        time.sleep(0.2)
        assert len(pending()) == 1

        client.post(
            f"/suggestions/{card['data']['id']}/action",
            json={"action": "added"},
            headers=owner,
        )
    events = client.get(f"/workspaces/{ws['id']}/events", headers=owner).json()
    assert [e["status"] for e in events] == ["cancelled"]


def test_events_and_todos_crud(client, login):
    ws, _ch, owner, member = _setup(client, login, ai=False)
    wid = ws["id"]
    ev = client.post(
        f"/workspaces/{wid}/events", json={"title": "발표", "date": THU}, headers=member
    ).json()
    assert ev["source"] == "manual" and ev["status"] == "confirmed"
    assert (
        client.patch(
            f"/events/{ev['id']}", json={"location": "301호"}, headers=owner
        ).json()["location"]
        == "301호"
    )
    assert client.delete(f"/events/{ev['id']}", headers=owner).status_code == 204

    assert (
        client.post(
            f"/workspaces/{wid}/todos",
            json={"title": "x", "assignee_id": 999},
            headers=owner,
        ).status_code
        == 422
    )
    me = client.get("/auth/me", headers=member).json()
    todo = client.post(
        f"/workspaces/{wid}/todos",
        json={"title": "PPT", "assignee_id": me["id"]},
        headers=owner,
    ).json()
    assert client.patch(
        f"/todos/{todo['id']}", json={"done": True}, headers=member
    ).json()["done"]
    assert (
        client.patch(
            f"/todos/{todo['id']}", json={"done": True}, headers=login("z@x.com")
        ).status_code
        == 404
    )
