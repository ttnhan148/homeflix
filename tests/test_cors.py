EVIL = "https://evil.nhant.com"


def test_preflight_does_not_allow_credentials_or_echo_origin(make_client):
    r = make_client().options("/api/admin/revoke-others", headers={
        "Origin": EVIL, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert r.headers.get("access-control-allow-credentials") != "true"
    assert r.headers.get("access-control-allow-origin") != EVIL


def test_simple_get_does_not_allow_credentials_or_echo_origin(make_client):
    r = make_client().get("/api/admin/devices", headers={"Origin": EVIL})
    assert r.headers.get("access-control-allow-credentials") != "true"
    assert r.headers.get("access-control-allow-origin") != EVIL
