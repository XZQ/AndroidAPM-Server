from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from androidapm_server.config import Settings, get_settings
from androidapm_server.db.base import Base
from androidapm_server.db.models import AuditLog, QueryKey, Tenant
from androidapm_server.db.session import get_session
from androidapm_server.errors import ApiError
from androidapm_server.main import create_app
from androidapm_server.query_auth import generate_query_key
from androidapm_server.web_auth import (
    WEB_CSRF_COOKIE,
    WEB_CSRF_HEADER,
    WEB_SESSION_COOKIE,
    WEB_SESSION_VERSION,
    WebSessionClaims,
    _verify_csrf,
    decode_web_session,
)

TEST_WEB_KEY_B64 = base64.b64encode(b"web-session-test-key-material-32-bytes").decode()
TEST_CURSOR_KEY_B64 = base64.b64encode(b"query-cursor-test-key-material-32-bytes").decode()
NOW_MS = int(datetime.now(UTC).timestamp() * 1_000)


@pytest_asyncio.fixture
async def web_api(
    tmp_path: Path,
) -> AsyncIterator[
    tuple[AsyncClient, async_sessionmaker[AsyncSession], dict[str, str], Settings]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    credentials: dict[str, str] = {}
    async with factory() as session:
        session.add(Tenant(id="tenant-web", name="Tenant Web"))
        for role in ("viewer", "investigator"):
            key_id, plaintext, key_hash = generate_query_key()
            credentials[role] = plaintext
            session.add(
                QueryKey(
                    key_id=key_id,
                    tenant_id="tenant-web",
                    key_hash=key_hash,
                    app_id="com.example.web",
                    environment="production",
                    role=role,
                )
            )
        await session.commit()

    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Console</title>", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    (assets / "app.js").write_text("export {};", encoding="utf-8")
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        environment="test",
        web_dist_path=dist,
        web_session_hmac_key_b64=TEST_WEB_KEY_B64,
        query_cursor_hmac_key_b64=TEST_CURSOR_KEY_B64,
        query_max_rows=100,
    )

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory, credentials, settings
    await engine.dispose()


@pytest.mark.asyncio
async def test_query_key_exchanges_for_http_only_session_without_secret_leak(
    web_api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict[str, str], Settings],
) -> None:
    client, factory, credentials, _ = web_api
    response = await client.post(
        "/v1/web/session",
        json={"queryKey": credentials["viewer"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["scope"] == {
        "tenantId": "tenant-web",
        "appId": "com.example.web",
        "environment": "production",
    }
    assert response.json()["role"] == "viewer"
    # Runtime deployment label is distinct from the fixed Query Key data scope.
    assert response.json()["environment"] == "test"
    assert credentials["viewer"] not in response.text
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(value for value in cookies if value.startswith(WEB_SESSION_COOKIE))
    csrf_cookie = next(value for value in cookies if value.startswith(WEB_CSRF_COOKIE))
    assert "HttpOnly" in session_cookie
    assert "SameSite=strict" in session_cookie
    assert "HttpOnly" not in csrf_cookie
    assert response.headers["cache-control"] == "private, no-store"

    scoped_query = await client.get(
        "/v1/query/data-quality",
        params={"fromMs": NOW_MS - 60_000, "toMs": NOW_MS},
    )
    assert scoped_query.status_code == 200, scoped_query.text
    assert scoped_query.json()["state"] == "NO_DATA"

    async with factory() as session:
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "web.session.create")
        )
        assert audit is not None
        assert credentials["viewer"] not in str(audit.details_json)


@pytest.mark.asyncio
async def test_cookie_writes_require_bound_csrf_and_bearer_compatibility_remains(
    web_api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict[str, str], Settings],
) -> None:
    client, _, credentials, _ = web_api
    login = await client.post(
        "/v1/web/session",
        json={"queryKey": credentials["investigator"]},
    )
    assert login.status_code == 200
    decision = _decision_body()

    missing_csrf = await client.post("/v1/query/release-decisions", json=decision)
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["code"] == "csrf_validation_failed"

    csrf = client.cookies.get(WEB_CSRF_COOKIE)
    assert csrf is not None
    created = await client.post(
        "/v1/query/release-decisions",
        headers={WEB_CSRF_HEADER: csrf},
        json=decision,
    )
    assert created.status_code == 201, created.text

    # Explicit API clients do not use ambient cookies and therefore keep Bearer semantics.
    bearer = await client.post(
        "/v1/query/release-decisions",
        headers={"Authorization": f"Bearer {credentials['investigator']}"},
        json={**decision, "reason": "A second explicit Bearer API decision"},
    )
    assert bearer.status_code == 201, bearer.text


@pytest.mark.asyncio
async def test_query_key_revocation_invalidates_an_existing_web_session(
    web_api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict[str, str], Settings],
) -> None:
    client, factory, credentials, _ = web_api
    login = await client.post("/v1/web/session", json={"queryKey": credentials["viewer"]})
    assert login.status_code == 200

    key_id = credentials["viewer"].split("_", maxsplit=2)[1]
    async with factory() as session:
        query_key = await session.get(QueryKey, key_id)
        assert query_key is not None
        query_key.active = False
        await session.commit()

    restored = await client.get("/v1/web/session")
    assert restored.status_code == 401
    assert restored.json()["code"] == "invalid_credential"


@pytest.mark.asyncio
async def test_logout_requires_csrf_and_clears_both_cookies(
    web_api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict[str, str], Settings],
) -> None:
    client, _, credentials, _ = web_api
    login = await client.post(
        "/v1/web/session",
        json={"queryKey": credentials["investigator"]},
    )
    assert login.status_code == 200
    without_csrf = await client.delete("/v1/web/session")
    assert without_csrf.status_code == 403

    csrf = client.cookies.get(WEB_CSRF_COOKIE)
    assert csrf is not None
    deleted = await client.delete("/v1/web/session", headers={WEB_CSRF_HEADER: csrf})
    assert deleted.status_code == 204
    assert client.cookies.get(WEB_SESSION_COOKIE) is None
    assert client.cookies.get(WEB_CSRF_COOKIE) is None


@pytest.mark.asyncio
async def test_web_shell_has_security_headers_and_assets_are_immutable(
    web_api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict[str, str], Settings],
) -> None:
    client, _, _, _ = web_api
    index = await client.get("/")
    assert index.status_code == 200
    assert index.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in index.headers["content-security-policy"]
    assert index.headers["x-content-type-options"] == "nosniff"

    favicon = await client.get("/favicon.svg")
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/svg+xml")
    assert favicon.headers["cache-control"] == "public, max-age=86400"

    asset = await client.get("/assets/app.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert (await client.get("/assets/../index.html")).status_code == 404


def test_production_settings_require_secure_cookie_and_strong_session_key() -> None:
    with pytest.raises(ValueError, match="secure cookies"):
        Settings(environment="production", web_session_hmac_key_b64=TEST_WEB_KEY_B64)
    with pytest.raises(ValueError, match="at least 32 bytes"):
        Settings(
            environment="production",
            web_session_cookie_secure=True,
            web_session_hmac_key_b64=base64.b64encode(b"short").decode(),
        )


def _session_settings() -> Settings:
    return Settings(_env_file=None, web_session_hmac_key_b64=TEST_WEB_KEY_B64)


def _signed_token(claims: dict[str, object], key_b64: str = TEST_WEB_KEY_B64) -> str:
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(base64.b64decode(key_b64), payload, hashlib.sha256).digest()

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{b64(payload)}.{b64(signature)}"


def _valid_claims() -> dict[str, object]:
    now = int(datetime.now(UTC).timestamp())
    return {
        "v": WEB_SESSION_VERSION,
        "kid": "0123456789abcdef01",
        "iat": now,
        "exp": now + 3_600,
        "csrf": hashlib.sha256(b"csrf-token").hexdigest(),
    }


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(lambda claims: claims.update(v=WEB_SESSION_VERSION + 1), id="version"),
        pytest.param(lambda claims: claims.update(kid="not-hex"), id="key-id"),
        pytest.param(lambda claims: claims.update(iat=claims["iat"] + 120), id="future-iat"),
        pytest.param(lambda claims: claims.update(exp=claims["iat"] - 1), id="expired"),
        pytest.param(lambda claims: claims.update(exp=claims["iat"] + 7_200), id="over-ttl"),
        pytest.param(lambda claims: claims.pop("csrf"), id="missing-claim"),
        pytest.param(lambda claims: claims.update(csrf="z" * 64), id="non-hex-csrf"),
    ],
)
def test_decode_web_session_rejects_invalid_claims(mutation: object) -> None:
    claims = _valid_claims()
    mutation(claims)  # type: ignore[operator]
    with pytest.raises(ApiError) as caught:
        decode_web_session(_signed_token(claims), _session_settings())
    assert caught.value.status_code == 401


def test_decode_web_session_rejects_tampered_signature_and_encoding() -> None:
    token = _signed_token(_valid_claims())
    payload, signature = token.split(".")
    flipped = signature[:-1] + ("A" if signature[-1] != "A" else "B")
    for candidate in (
        f"{payload}.{flipped}",
        f"{payload}={signature}",
        f"{payload}.{signature}!",
        f"{payload}",
    ):
        with pytest.raises(ApiError) as caught:
            decode_web_session(candidate, _session_settings())
        assert caught.value.status_code == 401
    assert decode_web_session(token, _session_settings()).key_id == "0123456789abcdef01"


@pytest.mark.parametrize(
    ("cookie_bytes", "header_bytes"),
    [(b"abc\xff", b"abc\xff"), (b"abc\xff", b"abc"), (b"abc", b"abc\xff")],
)
def test_non_ascii_csrf_values_fail_closed_as_403(cookie_bytes: bytes, header_bytes: bytes) -> None:
    """Corrupted latin-1 cookie/header bytes must not crash compare_digest into a 500."""
    claims = WebSessionClaims(
        key_id="0123456789abcdef01",
        issued_at=int(datetime.now(UTC).timestamp()),
        expires_at=int(datetime.now(UTC).timestamp()) + 3_600,
        csrf_sha256=hashlib.sha256(b"abc").hexdigest(),
    )
    scope = {
        "type": "http",
        "headers": [
            (b"cookie", WEB_CSRF_COOKIE.encode() + b"=" + cookie_bytes),
            (WEB_CSRF_HEADER.encode(), header_bytes),
        ],
    }
    with pytest.raises(ApiError) as caught:
        _verify_csrf(Request(scope), claims)
    assert caught.value.status_code == 403
    assert caught.value.code == "csrf_validation_failed"


@pytest.mark.asyncio
async def test_csrf_token_is_bound_to_its_own_session(
    web_api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict[str, str], Settings],
) -> None:
    client, _, credentials, _ = web_api
    first = await client.post(
        "/v1/web/session", json={"queryKey": credentials["investigator"]}
    )
    assert first.status_code == 200
    foreign_csrf = client.cookies.get(WEB_CSRF_COOKIE)
    assert foreign_csrf is not None
    second = await client.post(
        "/v1/web/session", json={"queryKey": credentials["investigator"]}
    )
    assert second.status_code == 200

    stolen = await client.post(
        "/v1/query/release-decisions",
        headers={WEB_CSRF_HEADER: foreign_csrf},
        json=_decision_body(),
    )
    assert stolen.status_code == 403
    assert stolen.json()["code"] == "csrf_validation_failed"

    own_csrf = client.cookies.get(WEB_CSRF_COOKIE)
    assert own_csrf is not None
    accepted = await client.post(
        "/v1/query/release-decisions",
        headers={WEB_CSRF_HEADER: own_csrf},
        json=_decision_body(),
    )
    assert accepted.status_code == 201, accepted.text


@pytest.mark.asyncio
async def test_expired_query_key_invalidates_an_existing_cookie_session(
    web_api: tuple[AsyncClient, async_sessionmaker[AsyncSession], dict[str, str], Settings],
) -> None:
    client, factory, credentials, _ = web_api
    login = await client.post("/v1/web/session", json={"queryKey": credentials["viewer"]})
    assert login.status_code == 200

    key_id = credentials["viewer"].split("_", maxsplit=2)[1]
    async with factory() as session:
        query_key = await session.get(QueryKey, key_id)
        assert query_key is not None
        query_key.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    restored = await client.get("/v1/web/session")
    assert restored.status_code == 401
    assert restored.json()["code"] == "invalid_credential"


def _decision_body() -> dict[str, object]:
    return {
        "releaseVersion": "2.0.0",
        "decision": "pause",
        "evidenceFromMs": NOW_MS - 60_000,
        "evidenceToMs": NOW_MS,
        "reason": "Pause while the release evidence is reviewed",
        "evidence": {
            "releaseState": "NO_DATA",
            "baselineState": "NO_DATA",
            "javaCrashEvents": None,
            "anrEvents": None,
            "affectedInstallations": None,
            "activeInstallations": None,
            "affectedInstallationRatio": None,
            "dataQualityState": "NO_DATA",
            "queryRequestId": "web-test-request",
        },
    }
