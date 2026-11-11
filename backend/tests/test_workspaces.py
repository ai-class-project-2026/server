def _login(client, email):
    r = client.post(
        "/auth/signup", json={"name": email[0], "email": email, "password": "password1"}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_workspace_flow(client):
    owner, other = _login(client, "o@x.com"), _login(client, "m@x.com")

    ws = client.post("/workspaces", json={"name": "팀플"}, headers=owner).json()
    assert ws["ai_schedule_on"] is False and ws["filter_level"] == 2
    wid, code = ws["id"], ws["invite_code"]

    assert [w["id"] for w in client.get("/workspaces", headers=owner).json()] == [wid]
    assert client.get("/workspaces", headers=other).json() == []
    assert client.get(f"/workspaces/{wid}", headers=other).status_code == 404

    assert client.post("/invites/nope", headers=other).status_code == 404
    for _ in range(2):  # joining twice is a no-op
        assert client.post(f"/invites/{code}", headers=other).json()["id"] == wid
    roles = [
        m["role"]
        for m in client.get(f"/workspaces/{wid}/members", headers=other).json()
    ]
    assert roles == ["owner", "member"]

    assert (
        client.patch(
            f"/workspaces/{wid}", json={"filter_level": 1}, headers=other
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/workspaces/{wid}", json={"filter_level": 5}, headers=owner
        ).status_code
        == 422
    )
    r = client.patch(
        f"/workspaces/{wid}",
        json={"filter_mode": "warn", "ai_schedule_on": True},
        headers=owner,
    )
    assert r.json()["filter_mode"] == "warn" and r.json()["ai_schedule_on"] is True
