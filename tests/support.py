"""Shared test setup: fixed config and repo root on sys.path.

kb/common.py reads the environment once at import time, so every test module
imports this before any project module.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEST_ENV = {
    "KB_API_TOKEN": "ci-api-token",
    "KB_SESSION_SECRET": "ci-session-secret-with-at-least-32-bytes",
    "KB_MCP_TOKEN": "ci-mcp-token",
    "KB_PUBLIC_BASE_URL": "https://kb.example.test",
    "KB_OIDC_ISSUER": "https://sso.example.test",
    "KB_OIDC_CLIENT_ID": "ci-client",
    "KB_OIDC_REDIRECT_URIS": "",
    "KB_RERANK_PROVIDER": "fastembed",
    "KB_RERANK_MIN_SCORE": "0.75",
    "KB_ANSWERABILITY_OVERRIDE": "0",
    "KB_DATA_DIR": str(ROOT / "tests" / "_no_data"),
}
for key, value in TEST_ENV.items():
    os.environ[key] = value
os.environ.pop("KB_API_ENV_FILE", None)
os.environ.pop("KB_API_TOKEN_FILE", None)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
