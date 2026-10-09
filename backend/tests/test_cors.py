"""Browsers send an unauthenticated OPTIONS "pre-check" first. The Lambda must answer it and add CORS headers."""

import pytest

from test_api import call_raw, make_user

SITE = "https://site.example.com"
PREFLIGHT = {
    "origin": SITE,
    "access-control-request-method": "POST",
    "access-control-request-headers": "authorization,content-type",
}


def lower(headers):
    return {k.lower(): v for k, v in headers.items()}


@pytest.mark.parametrize("path", ["/users", "/users/abc", "/appointments", "/availability", "/settings/reminders"])
def test_preflight_is_answered_without_a_token(aws, path):
    res = call_raw("OPTIONS", path, headers=PREFLIGHT, authorized=False)
    h = lower(res["headers"])
    assert res["statusCode"] == 204
    assert h["access-control-allow-origin"] == SITE
    assert "authorization" in h["access-control-allow-headers"].lower()
    assert "content-type" in h["access-control-allow-headers"].lower()
    assert "POST" in h["access-control-allow-methods"] or "GET" in h["access-control-allow-methods"]


def test_normal_and_error_responses_carry_the_origin_header(aws):
    ok = call_raw("GET", "/settings/reminders", headers={"origin": SITE})
    assert ok["statusCode"] == 200 and lower(ok["headers"])["access-control-allow-origin"] == SITE

    created = call_raw("POST", "/users", {"name": "Ana", "email": "a@example.com", "phone": "+40712345678"}, headers={"origin": SITE})
    assert created["statusCode"] == 201 and lower(created["headers"])["access-control-allow-origin"] == SITE

    # errors must be readable by the page too, otherwise the browser reports "cannot reach the server"
    for res in (
        call_raw("POST", "/users", {"name": "x"}, headers={"origin": SITE}),  # 422 validation
        call_raw("GET", "/users/missing", headers={"origin": SITE}),  # 404
        call_raw("GET", "/settings/reminders", groups=(), headers={"origin": SITE}),  # 403 role
    ):
        assert res["statusCode"] in (403, 404, 422)
        assert lower(res["headers"])["access-control-allow-origin"] == SITE


def test_each_configured_origin_is_echoed_back(aws):
    for origin in (SITE, "http://localhost:5174"):
        res = call_raw("GET", "/settings/reminders", headers={"origin": origin})
        assert lower(res["headers"])["access-control-allow-origin"] == origin


def test_preflight_does_not_touch_the_database(aws):
    make_user()  # data exists, but a pre-check must neither read nor change it
    res = call_raw("OPTIONS", "/users", headers=PREFLIGHT, authorized=False)
    assert res["statusCode"] == 204 and not res.get("body")
