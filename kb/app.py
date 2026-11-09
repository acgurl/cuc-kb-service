import hmac
import json
import secrets
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from urllib.parse import quote, urlencode

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from kb.auth import AuthError, issue_session, require_user, verify_oidc_token
from kb.common import (
    BASE_DIR,
    OIDC_CLIENT_ID,
    OIDC_CLIENT_SECRET,
    OIDC_ISSUER,
    OIDC_REDIRECT_URIS,
    PUBLIC_BASE_URL,
    SESSION_COOKIE,
    SESSION_TTL,
    USER_AGENT,
)
from kb.file_access import signed_url
from kb.search_core import KB

OAUTH_COOKIES = ("kb_oauth_state", "kb_oauth_nonce", "kb_oauth_next")
OAUTH_COOKIE_TTL = 600
LOCAL_HOST_MARKERS = ("127.0.0.1", "localhost", "[::1]")
UI_HTML = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")

# Built in the browser so #page=N survives the login round trip.
LOGIN_BOOTSTRAP = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>需要登录</title></head>
<body style="font-family:system-ui,sans-serif;padding:2rem;line-height:1.6">
<p>正在跳转到登录页…</p>
<p><a href="/auth/login">如果没有自动跳转，请点击登录</a></p>
<script>location.replace("/auth/login?next=" + encodeURIComponent(location.pathname + location.search + location.hash));</script>
</body>
</html>"""

kb = KB()


@asynccontextmanager
async def lifespan(app: FastAPI):
    kb.load()
    yield


app = FastAPI(title="CUC Knowledge Base", version="0.2.0", lifespan=lifespan)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    assistant: str | None = None
    doc_type: list[str] | None = None
    top_k: int = Field(default=5, ge=1, le=20)


def _sso_configured():
    return bool(OIDC_ISSUER and OIDC_CLIENT_ID and OIDC_REDIRECT_URIS)


def _safe_next(value):
    # Browsers treat "\" as "/" and drop tabs/newlines, so "/\evil" or "/\t/evil"
    # would become the protocol-relative "//evil"; real paths arrive percent-encoded.
    if (
        value
        and value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    ):
        return value
    return "/"


def _request_base(request: Request):
    return str(request.base_url).rstrip("/")


def _callback_uri(request: Request):
    return _request_base(request) + "/auth/callback"


def _registered_callback(request: Request):
    redirect_uri = _callback_uri(request)
    if redirect_uri not in OIDC_REDIRECT_URIS:
        raise HTTPException(status_code=400, detail="callback host is not registered")
    return redirect_uri


def _localize_file_url(item, request: Request):
    """Rewrite PUBLIC_BASE_URL file links to the host the caller actually used."""
    url = item.get("file_url") if isinstance(item, dict) else None
    if not url or not PUBLIC_BASE_URL:
        return
    base = _request_base(request)
    if any(marker in base for marker in LOCAL_HOST_MARKERS):
        return
    prefix = PUBLIC_BASE_URL + "/v1/files/"
    if url.startswith(prefix):
        item["file_url"] = base + "/v1/files/" + url[len(prefix):]


def _browser_login_available(request: Request):
    if request.method != "GET":
        return False
    accept = request.headers.get("accept", "").lower()
    if "text/html" not in accept and request.headers.get("sec-fetch-dest") != "document":
        return False
    return _sso_configured() and _callback_uri(request) in OIDC_REDIRECT_URIS


def _set_cookie(response, name, value, max_age, secure):
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _exchange_code(code, redirect_uri):
    form = urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": OIDC_CLIENT_ID,
            "client_secret": OIDC_CLIENT_SECRET,
        }
    ).encode()
    token_request = urllib.request.Request(
        OIDC_ISSUER + "/api/login/oauth/access_token",
        data=form,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(token_request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"token exchange failed: {exc}", flush=True)
        raise HTTPException(status_code=502, detail="token exchange failed")


@app.middleware("http")
async def no_store(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith(("/v1/", "/auth/")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "chunks": len(kb.chunks),
        "loaded": kb.loaded_at is not None,
    }


@app.get("/v1/meta")
def meta(user: dict = Depends(require_user)):
    return kb.meta()


@app.post("/v1/search")
def search(request: SearchRequest, http_request: Request, user: dict = Depends(require_user)):
    payload = kb.search(
        request.query,
        assistant=request.assistant,
        doc_types=request.doc_type,
        top_k=request.top_k,
    )
    for item in payload.get("results") or []:
        _localize_file_url(item.get("citation"), http_request)
    return payload


@app.get("/v1/documents")
def documents(http_request: Request, user: dict = Depends(require_user)):
    docs = kb.documents()
    for item in docs:
        _localize_file_url(item, http_request)
    return {"documents": docs}


@app.get("/v1/documents/{doc_id}/chunks")
def document_chunks(doc_id: str, http_request: Request, user: dict = Depends(require_user)):
    chunks = kb.document_chunks(doc_id)
    if not chunks:
        raise HTTPException(status_code=404, detail="document not found")
    payload = {
        "doc_id": doc_id,
        "page_count": kb.page_count(doc_id),
        "file_url": kb.file_url(doc_id),
        "source_url": chunks[0].get("source_url", ""),
        "publish_date": chunks[0].get("publish_date", ""),
        "date_basis": chunks[0].get("date_basis", ""),
        "chunks": chunks,
    }
    _localize_file_url(payload, http_request)
    return payload


@app.api_route("/v1/files/{doc_id}", methods=["GET", "HEAD"])
def file_redirect(doc_id: str, request: Request):
    try:
        require_user(request)
    except HTTPException:
        if _browser_login_available(request):
            return HTMLResponse(LOGIN_BOOTSTRAP)
        raise
    if doc_id == "qa-csv" or doc_id not in kb.doc_meta:
        raise HTTPException(status_code=404, detail="document not found")
    try:
        url = signed_url(doc_id)
    except Exception as exc:
        print(f"pre-authenticated request failed for {doc_id}: {exc}", flush=True)
        raise HTTPException(status_code=502, detail="file link unavailable")
    return RedirectResponse(
        url,
        status_code=302,
        headers={"Cache-Control": "private, no-store"},
    )


@app.get("/auth/login")
def auth_login(request: Request, next: str = "/"):
    if not _sso_configured():
        raise HTTPException(status_code=503, detail="SSO is not configured")
    redirect_uri = _registered_callback(request)

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    params = {
        "client_id": OIDC_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
    }
    response = RedirectResponse(
        OIDC_ISSUER + "/login/oauth/authorize?" + urlencode(params),
        status_code=302,
    )
    secure = redirect_uri.startswith("https://")
    for name, value in zip(OAUTH_COOKIES, (state, nonce, _safe_next(next))):
        _set_cookie(response, name, value, OAUTH_COOKIE_TTL, secure)
    return response


@app.get("/auth/callback")
def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    redirect_uri = _registered_callback(request)
    if error:
        return RedirectResponse("/?login_error=" + quote(error), status_code=302)
    if not code:
        raise HTTPException(status_code=400, detail="missing code")

    expected_state = request.cookies.get("kb_oauth_state") or ""
    if not expected_state or not hmac.compare_digest(expected_state, state):
        raise HTTPException(status_code=400, detail="invalid state")

    payload = _exchange_code(code, redirect_uri)
    id_token = payload.get("id_token") or payload.get("access_token")
    if not id_token:
        raise HTTPException(status_code=502, detail="identity token missing")
    try:
        claims = verify_oidc_token(id_token)
    except AuthError as exc:
        print(f"id token rejected: {exc}", flush=True)
        raise HTTPException(status_code=401, detail="invalid identity token")

    # The nonce cookie is always set by /auth/login, so the token must echo it.
    expected_nonce = request.cookies.get("kb_oauth_nonce") or ""
    if not expected_nonce or not hmac.compare_digest(
        expected_nonce, str(claims.get("nonce") or "")
    ):
        raise HTTPException(status_code=400, detail="invalid nonce")

    target = _safe_next(request.cookies.get("kb_oauth_next"))
    response = RedirectResponse(target, status_code=302)
    _set_cookie(
        response,
        SESSION_COOKIE,
        issue_session(claims),
        SESSION_TTL,
        redirect_uri.startswith("https://"),
    )
    for name in OAUTH_COOKIES:
        response.delete_cookie(name, path="/")
    return response


@app.get("/auth/me")
def auth_me(user: dict = Depends(require_user)):
    return user


@app.get("/auth/logout")
def auth_logout():
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/", response_class=HTMLResponse)
def index():
    return UI_HTML


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
