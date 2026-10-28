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

from auth import AuthError, issue_session, require_user, verify_oidc_token
from common import (
    OIDC_CLIENT_ID,
    OIDC_CLIENT_SECRET,
    OIDC_ISSUER,
    OIDC_REDIRECT_URIS,
    PUBLIC_BASE_URL,
    SESSION_COOKIE,
    SESSION_TTL,
)
from file_access import signed_url
from search_core import KB

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


def _safe_next(value):
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


def _callback_uri(request: Request):
    base = str(request.base_url).rstrip("/")
    return base + "/auth/callback"


def _localize_file_url(item, request):
    url = item.get("file_url") if isinstance(item, dict) else None
    if not url or not PUBLIC_BASE_URL:
        return
    base = str(request.base_url).rstrip("/")
    if "127.0.0.1" in base or "localhost" in base or "[::1]" in base:
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
    if not (OIDC_ISSUER and OIDC_CLIENT_ID and OIDC_REDIRECT_URIS):
        return False
    return _callback_uri(request) in OIDC_REDIRECT_URIS


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


def _cookie_kwargs(secure):
    return {
        "httponly": True,
        "secure": secure,
        "samesite": "lax",
        "path": "/",
    }


@app.middleware("http")
async def no_store(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/v1/") or path.startswith("/auth/"):
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
    if not (OIDC_ISSUER and OIDC_CLIENT_ID and OIDC_REDIRECT_URIS):
        raise HTTPException(status_code=503, detail="SSO is not configured")
    redirect_uri = _callback_uri(request)
    if redirect_uri not in OIDC_REDIRECT_URIS:
        raise HTTPException(status_code=400, detail="callback host is not registered")

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
    kwargs = _cookie_kwargs(secure)
    response.set_cookie("kb_oauth_state", state, max_age=600, **kwargs)
    response.set_cookie("kb_oauth_nonce", nonce, max_age=600, **kwargs)
    response.set_cookie("kb_oauth_next", _safe_next(next), max_age=600, **kwargs)
    return response


@app.get("/auth/callback")
def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    redirect_uri = _callback_uri(request)
    if redirect_uri not in OIDC_REDIRECT_URIS:
        raise HTTPException(status_code=400, detail="callback host is not registered")
    if error:
        return RedirectResponse("/?login_error=" + quote(error), status_code=302)
    if not code:
        raise HTTPException(status_code=400, detail="missing code")

    expected_state = request.cookies.get("kb_oauth_state") or ""
    if not expected_state or not hmac.compare_digest(expected_state, state):
        raise HTTPException(status_code=400, detail="invalid state")

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
            "User-Agent": "Mozilla/5.0 (compatible; CUC-KnowledgeBase/0.1)",
        },
    )
    try:
        with urllib.request.urlopen(token_request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"token exchange failed: {exc}", flush=True)
        raise HTTPException(status_code=502, detail="token exchange failed")

    id_token = payload.get("id_token") or payload.get("access_token")
    if not id_token:
        raise HTTPException(status_code=502, detail="identity token missing")
    try:
        claims = verify_oidc_token(id_token)
    except AuthError as exc:
        print(f"id token rejected: {exc}", flush=True)
        raise HTTPException(status_code=401, detail="invalid identity token")

    expected_nonce = request.cookies.get("kb_oauth_nonce")
    if expected_nonce and claims.get("nonce") and claims["nonce"] != expected_nonce:
        raise HTTPException(status_code=400, detail="invalid nonce")

    session = issue_session(claims)
    target = _safe_next(request.cookies.get("kb_oauth_next"))
    response = RedirectResponse(target, status_code=302)
    secure = redirect_uri.startswith("https://")
    response.set_cookie(
        SESSION_COOKIE,
        session,
        max_age=SESSION_TTL,
        **_cookie_kwargs(secure),
    )
    for name in ("kb_oauth_state", "kb_oauth_nonce", "kb_oauth_next"):
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


UI_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CUC 知识库检索</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; max-width: 960px; margin: 40px auto; padding: 0 20px; }
  header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: #888; font-size: 13px; margin-bottom: 24px; }
  .account { font-size: 13px; color: #666; }
  .account a { color: #2563eb; text-decoration: none; margin-left: 8px; }
  .row { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
  input, select, button { font-size: 14px; padding: 8px 10px; border-radius: 6px; border: 1px solid #bbb; background: transparent; color: inherit; }
  input#q { flex: 1; min-width: 260px; }
  button, .login { cursor: pointer; background: #2563eb; border-color: #2563eb; color: #fff; text-decoration: none; display: inline-block; border-radius: 6px; padding: 8px 14px; font-size: 14px; }
  button:disabled { opacity: .5; }
  .result { border: 1px solid #ddd; border-radius: 8px; padding: 14px 16px; margin-bottom: 12px; }
  .result h3 { font-size: 14px; margin: 0 0 6px; }
  .result h3 a { color: inherit; text-decoration: none; }
  .result h3 a:hover { color: #2563eb; }
  .cite { color: #888; font-size: 12px; margin-bottom: 8px; }
  .text { white-space: pre-wrap; line-height: 1.65; font-size: 14px; }
  .scores { color: #999; font-size: 11px; margin-top: 8px; }
  #status { color: #888; font-size: 13px; margin: 8px 0; min-height: 18px; }
  #loginBox { border: 1px solid #ddd; border-radius: 8px; padding: 24px; text-align: center; }
  #loginBox p { color: #666; font-size: 14px; }
  .hidden { display: none !important; }
</style>
</head>
<body>
<header>
  <div>
    <h1>CUC 知识库检索</h1>
    <div class="sub">混合检索：向量 + BM25 + RRF。结果包含文档名、条款和页码。</div>
  </div>
  <div class="account" id="account"></div>
</header>
<div id="loginBox" class="hidden">
  <p>需要登录后才能检索知识库。</p>
  <a class="login" id="loginLink" href="/auth/login?next=/">使用 acgurl SSO 登录</a>
</div>
<div id="app" class="hidden">
  <div class="row">
    <select id="assistant"><option value="">全部助手</option></select>
    <select id="docType"><option value="">全部类型</option></select>
  </div>
  <div class="row">
    <input id="q" placeholder="例如：如何申请成绩单？宿舍床尺寸是多少？" onkeydown="if(event.key==='Enter')search()">
    <button id="btn" onclick="search()">检索</button>
  </div>
  <div id="status"></div>
  <div id="results"></div>
</div>
<script>
const assistantEl = document.getElementById('assistant');
const docTypeEl = document.getElementById('docType');
const statusEl = document.getElementById('status');
const resultsEl = document.getElementById('results');
const btn = document.getElementById('btn');
const accountEl = document.getElementById('account');
const loginBox = document.getElementById('loginBox');
const appBox = document.getElementById('app');

function goLogin() {
  location.href = '/auth/login?next=' + encodeURIComponent(location.pathname + location.search);
}

function fillSelect(select, values, placeholder) {
  select.replaceChildren();
  const first = document.createElement('option');
  first.value = '';
  first.textContent = placeholder;
  select.appendChild(first);
  for (const value of values) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  }
}

async function loadMeta() {
  const res = await fetch('/v1/meta');
  if (res.status === 401) { goLogin(); return; }
  if (!res.ok) { statusEl.textContent = '加载失败：' + res.status; return; }
  const data = await res.json();
  fillSelect(assistantEl, data.assistants, '全部助手');
  fillSelect(docTypeEl, data.doc_types, '全部类型');
  statusEl.textContent = `已加载 ${data.documents} 篇文档 / ${data.chunks} 个知识块，模型 ${data.model}`;
}

async function init() {
  const params = new URLSearchParams(location.search);
  if (params.get('login_error')) {
    statusEl.textContent = '登录失败：' + params.get('login_error');
  }
  const res = await fetch('/auth/me');
  if (!res.ok) {
    loginBox.classList.remove('hidden');
    accountEl.textContent = '';
    return;
  }
  const user = await res.json();
  loginBox.classList.add('hidden');
  appBox.classList.remove('hidden');
  accountEl.replaceChildren();
  if (!user.service) {
    const name = document.createElement('span');
    name.textContent = user.name || user.email || '已登录';
    const logout = document.createElement('a');
    logout.href = '/auth/logout';
    logout.textContent = '退出';
    accountEl.append(name, logout);
  }
  loadMeta();
}

async function search() {
  const query = document.getElementById('q').value.trim();
  if (!query) return;
  btn.disabled = true;
  statusEl.textContent = '检索中…';
  resultsEl.replaceChildren();
  const body = { query, top_k: 5 };
  if (assistantEl.value) body.assistant = assistantEl.value;
  if (docTypeEl.value) body.doc_type = [docTypeEl.value];
  try {
    const res = await fetch('/v1/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (res.status === 401) { goLogin(); return; }
    if (!res.ok) { statusEl.textContent = '请求失败：' + res.status; return; }
    const data = await res.json();
    if (data.no_answer) {
      statusEl.textContent = `无可靠结果（置信度 ${data.confidence}，${data.took_ms}ms）`;
      return;
    }
    statusEl.textContent = `${data.results.length} 条结果，${data.took_ms}ms`;
    for (const item of data.results) {
      const card = document.createElement('div');
      card.className = 'result';
      const h = document.createElement('h3');
      if (item.citation.file_url) {
        const link = document.createElement('a');
        link.href = item.citation.file_url;
        link.target = '_blank';
        link.rel = 'noopener';
        link.textContent = item.citation.doc_title || '(untitled)';
        h.appendChild(link);
      } else {
        h.textContent = item.citation.doc_title || '(untitled)';
      }
      const cite = document.createElement('div');
      cite.className = 'cite';
      const parts = [];
      if (item.citation.doc_no) parts.push(item.citation.doc_no);
      if (item.citation.clause_no) parts.push(item.citation.clause_no);
      if (item.citation.section_path && item.citation.section_path.length) parts.push(item.citation.section_path.join(' > '));
      if (item.citation.page_start) parts.push(`第 ${item.citation.page_start} 页`);
      if (item.citation.source_file) parts.push(item.citation.source_file);
      cite.textContent = parts.join(' · ');
      const text = document.createElement('div');
      text.className = 'text';
      text.textContent = item.text;
      const scores = document.createElement('div');
      scores.className = 'scores';
      scores.textContent = `RRF ${item.score} · dense ${item.dense_score} · bm25 ${item.bm25_score}`;
      card.append(h, cite, text, scores);
      resultsEl.appendChild(card);
    }
  } catch (err) {
    statusEl.textContent = '请求出错：' + err;
  } finally {
    btn.disabled = false;
  }
}

init();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
