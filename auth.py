import hmac
import json
import threading
import time
import urllib.request

import jwt
from fastapi import HTTPException, Request

from common import (
    API_TOKEN,
    OIDC_CLIENT_ID,
    OIDC_ISSUER,
    SESSION_COOKIE,
    SESSION_SECRET,
    SESSION_TTL,
)

JWKS_URL = OIDC_ISSUER + "/.well-known/jwks" if OIDC_ISSUER else ""
USER_AGENT = "Mozilla/5.0 (compatible; CUC-KnowledgeBase/0.1)"
JWKS_TTL = 3600
ALLOWED_ALGS = ["RS256", "RS512"]

_jwks_lock = threading.Lock()
_jwks_keys = {}
_jwks_fetched_at = 0.0


class AuthError(Exception):
    pass


def _fetch_jwks():
    if not JWKS_URL:
        raise AuthError("OIDC issuer is not configured")
    request = urllib.request.Request(
        JWKS_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))
    keys = {}
    for item in body.get("keys") or []:
        kid = item.get("kid")
        if not kid or item.get("kty") != "RSA":
            continue
        keys[kid] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(item))
    if not keys:
        raise AuthError("no RSA keys in JWKS")
    return keys


def _get_key(kid):
    global _jwks_keys, _jwks_fetched_at
    with _jwks_lock:
        fresh = _jwks_keys and time.time() - _jwks_fetched_at < JWKS_TTL
        if fresh and kid in _jwks_keys:
            return _jwks_keys[kid]
        keys = _fetch_jwks()
        _jwks_keys = keys
        _jwks_fetched_at = time.time()
        key = keys.get(kid)
        if key is None:
            raise AuthError("unknown signing key")
        return key


def verify_oidc_token(token):
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm not in ALLOWED_ALGS or not header.get("kid"):
            raise AuthError("unsupported token header")
        key = _get_key(header["kid"])
        claims = jwt.decode(
            token,
            key,
            algorithms=ALLOWED_ALGS,
            audience=OIDC_CLIENT_ID,
            issuer=OIDC_ISSUER,
            options={"require": ["exp", "iat", "iss", "sub", "aud"]},
        )
    except AuthError:
        raise
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc
    return claims


def issue_session(claims, ttl=None):
    if not SESSION_SECRET:
        raise RuntimeError("KB_SESSION_SECRET is not configured")
    now = int(time.time())
    payload = {
        "sub": claims["sub"],
        "name": claims.get("name") or claims.get("preferred_username") or claims.get("email") or "",
        "email": claims.get("email") or "",
        "iat": now,
        "exp": now + (ttl or SESSION_TTL),
    }
    return jwt.encode(payload, SESSION_SECRET, algorithm="HS256")


def read_session(token):
    if not SESSION_SECRET:
        raise AuthError("session secret is not configured")
    try:
        return jwt.decode(token, SESSION_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc


def session_user(claims, service=False):
    return {
        "sub": claims["sub"],
        "name": claims.get("name") or claims.get("email") or claims["sub"],
        "email": claims.get("email") or "",
        "service": service,
    }


def require_user(request: Request):
    authorization = request.headers.get("authorization", "")
    if API_TOKEN and hmac.compare_digest(authorization, f"Bearer {API_TOKEN}"):
        return {"sub": "service", "name": "service", "email": "", "service": True}

    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        try:
            return session_user(verify_oidc_token(token))
        except AuthError:
            raise HTTPException(
                status_code=401,
                detail="invalid token",
                headers={"WWW-Authenticate": 'Bearer realm="kb"'},
            )

    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        try:
            return session_user(read_session(cookie))
        except AuthError:
            pass

    raise HTTPException(
        status_code=401,
        detail="login required",
        headers={"WWW-Authenticate": 'Bearer realm="kb"'},
    )
