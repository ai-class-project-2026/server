from app import auth


def _me(client, token):
    return client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})


def test_signup_login_me(client):
    r = client.post(
        "/auth/signup",
        json={"name": "민준", "email": "A@x.com", "password": "password1"},
    )
    assert r.status_code == 201
    assert _me(client, r.json()["access_token"]).json()["email"] == "a@x.com"

    dup = client.post(
        "/auth/signup", json={"name": "b", "email": "a@x.com", "password": "password1"}
    )
    assert dup.status_code == 409

    assert (
        client.post(
            "/auth/login", json={"email": "a@x.com", "password": "wrong-pass"}
        ).status_code
        == 401
    )
    token = client.post(
        "/auth/login", json={"email": "a@x.com", "password": "password1"}
    ).json()["access_token"]
    assert _me(client, token).status_code == 200

    assert _me(client, "garbage").status_code == 401
    assert client.get("/auth/me").status_code in (401, 403)


def test_google_links_and_locks_out_password(client, monkeypatch):
    client.post(
        "/auth/signup", json={"name": "x", "email": "s@x.com", "password": "password1"}
    )
    claims = {"sub": "g1", "email": "s@x.com", "email_verified": True, "name": "서연"}
    monkeypatch.setattr(auth, "verify_google_token", lambda _: claims)

    t1 = client.post("/auth/google", json={"id_token": "t"}).json()["access_token"]
    t2 = client.post("/auth/google", json={"id_token": "t"}).json()["access_token"]
    assert _me(client, t1).json()["id"] == _me(client, t2).json()["id"]
    # pre-registered password no longer works
    assert (
        client.post(
            "/auth/login", json={"email": "s@x.com", "password": "password1"}
        ).status_code
        == 401
    )

    monkeypatch.setattr(
        auth,
        "verify_google_token",
        lambda _: {**claims, "sub": "g2", "email": "n@x.com", "email_verified": False},
    )
    assert client.post("/auth/google", json={"id_token": "t"}).status_code == 401


def test_google_requires_client_id(client):
    assert client.post("/auth/google", json={"id_token": "t"}).status_code == 503
