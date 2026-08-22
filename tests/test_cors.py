"""The browser side of this service, which no other test covers.

The five-project demo at https://erikhill.dev/stack/ calls this API from the
page. CORS is the only thing standing between "the fifth stage lights" and "the
fifth stage silently never runs", and it is configuration, so nothing in the
Python suite noticed when the estate moved off egnaro9.github.io and the
allowlist did not follow.
"""

import pytest
from fastapi.testclient import TestClient

from evalhistory.app import create_app

BROWSER_ORIGINS = [
    "https://erikhill.dev",       # the live stack demo
    "https://egnaro9.github.io",  # the github.io host, still redirecting here
]


@pytest.mark.parametrize("origin", BROWSER_ORIGINS)
def test_default_allowlist_admits_the_browser_origins(origin):
    """A GET carrying Origin must come back with that origin echoed.

    Without the echo the browser discards a 200 the server already sent, so
    curl looks healthy while the page is dead. That is exactly how this went
    unnoticed: the endpoint was never down, only unreadable from the one place
    that reads it.
    """
    client = TestClient(create_app())
    r = client.get("/runs?limit=1", headers={"Origin": origin})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == origin


def test_preflight_is_answered_for_the_live_demo():
    """The demo issues a plain GET, but a preflight must not 400 either."""
    client = TestClient(create_app())
    r = client.options(
        "/runs",
        headers={
            "Origin": "https://erikhill.dev",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") == "https://erikhill.dev"


def test_an_unlisted_origin_is_still_refused():
    """Guards the fix: allow_origins must not become a wildcard."""
    client = TestClient(create_app())
    r = client.get("/runs?limit=1", headers={"Origin": "https://not-erik.example"})
    assert r.headers.get("access-control-allow-origin") is None
