

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
