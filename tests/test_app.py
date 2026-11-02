"""FastAPI app and auth, with the KB stubbed (no Qdrant, models or OCI calls).

TestClient is used without a context manager, so the lifespan hook that loads
the index never runs.
"""

import json
import time
import unittest
from unittest import mock

import jwt
from fastapi.testclient import TestClient

from tests import support

from kb import app as app_module
from kb import auth

TOKEN = support.TEST_ENV["KB_API_TOKEN"]
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def fake_search(query, assistant=None, doc_types=None, top_k=5):
    return {
        "query": query,
        "results": [
            {
                "chunk_id": "c1",
                "doc_id": "d1",
                "text": "床尺寸 0.9m×2.0m",
                "citation": {"file_url": "https://kb.example.test/v1/files/d1#page=3"},
            }
        ][:top_k],
        "no_answer": False,
        "confidence": 0.9,
        "took_ms": 1,
    }


class SessionTokenTest(unittest.TestCase):
    def test_round_trip(self):
        token = auth.issue_session({"sub": "u1", "preferred_username": "alice"})
        claims = auth.read_session(token)
        self.assertEqual((claims["sub"], claims["name"]), ("u1", "alice"))

    def test_rejects_foreign_signature_and_expiry(self):
        forged = jwt.encode({"sub": "u1", "exp": int(time.time()) + 60}, "other-secret-of-sufficient-length", algorithm="HS256")
        with self.assertRaises(auth.AuthError):
            auth.read_session(forged)
        expired = auth.issue_session({"sub": "u1"}, ttl=-10)
        with self.assertRaises(auth.AuthError):
            auth.read_session(expired)

    def test_oidc_rejects_symmetric_or_kidless_tokens(self):
        # An HS256 token must never reach key lookup (algorithm confusion).
        hs = jwt.encode({"sub": "x"}, "k" * 32, algorithm="HS256", headers={"kid": "a"})
        none = jwt.encode({"sub": "x"}, None, algorithm="none")
        with mock.patch.object(auth, "_get_key") as get_key:
            for token in (hs, none, "not-a-jwt"):
                with self.subTest(token=token[:20]), self.assertRaises(auth.AuthError):
                    auth.verify_oidc_token(token)
        get_key.assert_not_called()


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app)
        patcher = mock.patch.object(app_module.kb, "search", side_effect=fake_search)
        self.search = patcher.start()
        self.addCleanup(patcher.stop)

    def test_healthz_is_public(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_protected_routes_require_auth(self):
        for method, path in (
            ("get", "/v1/meta"),
            ("post", "/v1/search"),
            ("get", "/v1/documents"),
            ("get", "/v1/documents/d1/chunks"),
            ("get", "/v1/files/d1"),
            ("get", "/auth/me"),
        ):
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, headers={"Authorization": "Bearer wrong"})
                self.assertEqual(response.status_code, 401)
        self.search.assert_not_called()

    def test_session_cookie_authenticates(self):
        self.client.cookies.set(auth.SESSION_COOKIE, auth.issue_session({"sub": "u1", "email": "a@b"}))
        response = self.client.get("/auth/me")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sub"], "u1")

    def test_search(self):
        response = self.client.post("/v1/search", headers=AUTH, json={"query": "床尺寸", "top_k": 1, "doc_type": ["制度"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.search.assert_called_once_with("床尺寸", assistant=None, doc_types=["制度"], top_k=1)
        self.assertEqual(response.json()["results"][0]["doc_id"], "d1")

    def test_search_rewrites_file_url_to_request_host(self):
        response = self.client.post(
            "/v1/search", headers={**AUTH, "Host": "kb.other.test"}, json={"query": "床尺寸"}
        )
        url = response.json()["results"][0]["citation"]["file_url"]
        self.assertEqual(url, "http://kb.other.test/v1/files/d1#page=3")

    def test_search_validation(self):
        for body in ({"query": ""}, {"query": "x" * 501}, {"query": "q", "top_k": 0}, {"query": "q", "top_k": 21}, {}):
            with self.subTest(body=json.dumps(body)[:40]):
                response = self.client.post("/v1/search", headers=AUTH, json=body)
                self.assertEqual(response.status_code, 422)
        self.search.assert_not_called()

    def test_file_redirect_unknown_doc(self):
        with mock.patch.object(app_module, "signed_url") as signed_url:
            for doc_id in ("missing", "qa-csv"):
                with self.subTest(doc_id=doc_id):
                    self.assertEqual(self.client.get(f"/v1/files/{doc_id}", headers=AUTH).status_code, 404)
        signed_url.assert_not_called()

    def test_file_redirect_signs_known_doc(self):
        with mock.patch.dict(app_module.kb.doc_meta, {"d1": {"doc_id": "d1"}}), mock.patch.object(
            app_module, "signed_url", return_value="https://objectstorage.example.test/p/abc"
        ):
            response = self.client.get("/v1/files/d1", headers=AUTH, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "https://objectstorage.example.test/p/abc")

    def test_openapi_paths_exist_in_app(self):
        spec = json.loads((support.ROOT / "agent" / "openapi.json").read_text(encoding="utf-8"))
        routes = {
            (route.path, method.lower())
            for route in app_module.app.routes
            for method in getattr(route, "methods", None) or ()
        }
        for path, methods in spec["paths"].items():
            for method in methods:
                with self.subTest(path=path, method=method):
                    self.assertIn((path, method), routes)


class SafeNextTest(unittest.TestCase):
    def test_only_local_paths(self):
        self.assertEqual(app_module._safe_next("/v1/files/d1?x=1"), "/v1/files/d1?x=1")
        self.assertEqual(app_module._safe_next("/?q=%5C#page=2"), "/?q=%5C#page=2")
        for value in (
            "", None, "//evil.test/x", "https://evil.test", "javascript:alert(1)",
            "/\\evil.test", "/\\/evil.test", "/\t/evil.test", "/\n/evil.test", "/\x7f",
        ):
            with self.subTest(value=value):
                self.assertEqual(app_module._safe_next(value), "/")

    def test_login_round_trip_never_leaves_site(self):
        client = TestClient(app_module.app)
        with mock.patch.object(app_module, "OIDC_REDIRECT_URIS", ["http://testserver/auth/callback"]):
            for next_value, expected in (("/\\evil.test", "/"), ("/v1/files/d1", "/v1/files/d1")):
                with self.subTest(next=next_value):
                    client.cookies.clear()
                    login = client.get("/auth/login", params={"next": next_value}, follow_redirects=False)
                    self.assertEqual(login.status_code, 302)
                    state = client.cookies.get("kb_oauth_state")
                    claims = {"sub": "u1", "nonce": client.cookies.get("kb_oauth_nonce")}
                    with mock.patch.object(app_module, "_exchange_code", return_value={"id_token": "t"}), \
                            mock.patch.object(app_module, "verify_oidc_token", return_value=claims):
                        callback = client.get(
                            "/auth/callback", params={"code": "c", "state": state}, follow_redirects=False
                        )
                    self.assertEqual(callback.status_code, 302)
                    self.assertEqual(callback.headers["location"], expected)


if __name__ == "__main__":
    unittest.main()
