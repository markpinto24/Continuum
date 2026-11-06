"""The guards in front of every route: body size, request ids, what a deployment
exposes by default, and how sign-in failures are counted per client."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, Request

from continuum.config import Settings, get_settings
from continuum.core.middleware import BodyLimitMiddleware, RequestContextMiddleware
from continuum.services.limits import LoginThrottle


def guarded_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodyLimitMiddleware, default=1000, overrides={"/upload": 5000})
    app.add_middleware(RequestContextMiddleware)

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"read": len(await request.body())}

    @app.post("/upload")
    async def upload(request: Request) -> dict[str, int]:
        return {"read": len(await request.body())}

    @app.get("/id")
    async def request_id(request: Request) -> dict[str, str]:
        return {"id": request.state.request_id}

    return app


async def post(app: FastAPI, path: str, **kwargs) -> httpx.Response:  # noqa: ANN003
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, **kwargs)


async def test_a_body_over_the_limit_is_refused_before_it_is_read():
    app = guarded_app()
    assert (await post(app, "/echo", content=b"x" * 999)).status_code == 200
    response = await post(app, "/echo", content=b"x" * 1001)
    assert response.status_code == 413 and "too large" in response.json()["detail"]


async def test_a_chunked_body_is_counted_as_it_arrives():
    async def chunks():  # no Content-Length: the size is only known by counting
        for _ in range(5):
            yield b"x" * 400

    response = await post(guarded_app(), "/echo", content=chunks())
    assert response.status_code == 413


async def test_an_upload_route_has_its_own_larger_limit():
    app = guarded_app()
    assert (await post(app, "/upload", content=b"x" * 4000)).status_code == 200
    assert (await post(app, "/upload", content=b"x" * 6000)).status_code == 413


@pytest.mark.parametrize(
    ("offered", "kept"),
    [("abc-123.def_4", True), ("x" * 65, False), ("bad id\nINJECTED log line", False), ("", False)],
)
async def test_a_client_request_id_is_kept_only_if_it_looks_like_one(offered, kept):
    transport = httpx.ASGITransport(app=guarded_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/id", headers={"x-request-id": offered} if offered else {})
    assert (response.json()["id"] == offered) is kept


def test_web_setup_is_on_only_for_local_development_unless_set():
    assert Settings(environment="local").auth_allow_web_setup is True
    assert Settings(environment="production").auth_allow_web_setup is False
    assert Settings(environment="production", auth_allow_web_setup=True).auth_allow_web_setup


def test_the_interactive_docs_exist_only_locally(monkeypatch):
    from continuum.main import create_app

    for environment, exposed in (("local", True), ("production", False)):
        monkeypatch.setenv("ENVIRONMENT", environment)
        get_settings.cache_clear()
        app = create_app()
        assert (app.openapi_url is not None) is exposed
        assert (app.docs_url is not None) is exposed
    get_settings.cache_clear()


def test_one_ipv6_subscriber_is_one_client_for_the_sign_in_lockout():
    a = LoginThrottle.keys("m@example.com", "2001:db8:1:2:aaaa::1")
    b = LoginThrottle.keys("m@example.com", "2001:db8:1:2:ffff::9")
    elsewhere = LoginThrottle.keys("m@example.com", "2001:db8:1:3::1")
    assert a[1] == b[1] == "ip:2001:db8:1:2::/64"
    assert elsewhere[1] != a[1]
    assert LoginThrottle.keys("m@example.com", "10.0.0.9")[1] == "ip:10.0.0.9"
