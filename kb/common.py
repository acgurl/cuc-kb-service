"""Shared configuration: every environment variable and its default lives here."""

import os
import re
from pathlib import Path


def _env(name, default=""):
    return os.getenv(name, default).strip()


def _env_int(name, default):
    return int(_env(name) or default)


def _env_float(name, default):
    return float(_env(name) or default)


def _env_bool(name, default):
    return (_env(name) or ("1" if default else "0")) == "1"


def _env_list(name, default=""):
    return [item.strip() for item in _env(name, default).split(",") if item.strip()]


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(_env("KB_DATA_DIR") or BASE_DIR / "data")
FASTEMBED_CACHE = Path(_env("FASTEMBED_CACHE") or BASE_DIR / ".cache/fastembed")
USER_AGENT = "Mozilla/5.0 (compatible; CUC-KnowledgeBase/0.1)"

# Core
COLLECTION = _env("KB_COLLECTION", "cuc_kb")
EMBED_MODEL = _env("KB_EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

# Query-side embedding provider. "fastembed" keeps the local model; the cloud
# providers call an OpenAI-compatible /embeddings endpoint and must match the
# model/dimension used to build the collection (see scripts/batch_embed.py).
CLOUD_EMBED_PROVIDERS = ("openai", "cloud", "openai-compatible")
EMBED_PROVIDER = _env("KB_EMBED_PROVIDER", "fastembed").lower()
EMBED_API_BASE = _env("EMBED_API_BASE", "https://dashscope.aliyuncs.com").rstrip("/")
EMBED_API_PATH = _env("EMBED_API_PATH", "/compatible-mode/v1/embeddings")
EMBED_API_MODEL = _env("EMBED_API_MODEL", "qwen3.7-text-embedding")
EMBED_API_KEY = _env("EMBED_API_KEY")
EMBED_API_TIMEOUT = _env_float("EMBED_API_TIMEOUT", 10)
EMBED_DIM = _env_int("KB_EMBED_DIM", 1024)
# Applied to queries only; documents are embedded plain (asymmetric retrieval).
EMBED_QUERY_INSTRUCT = _env(
    "KB_EMBED_QUERY_INSTRUCT",
    "Given a Chinese university regulation search query, retrieve relevant passages that answer the query",
)
# LRU size for query embeddings; identical queries reuse the same vector and
# skip one cloud round-trip. 0 disables the cache.
EMBED_QUERY_CACHE_SIZE = _env_int("KB_EMBED_QUERY_CACHE", 2048)
API_TOKEN = _env("KB_API_TOKEN")
PUBLIC_BASE_URL = _env("KB_PUBLIC_BASE_URL").rstrip("/")
FILE_OBJECT_PREFIX = _env("KB_FILE_OBJECT_PREFIX", "cuc-kb/docs").strip("/")

# Qdrant
QDRANT_URL = _env("QDRANT_URL", "http://127.0.0.1:6333")
QDRANT_API_KEY = _env("QDRANT_API_KEY")

# Rerank
CLOUD_RERANK_PROVIDERS = ("openai", "cloud", "openai-compatible")
RERANK_PROVIDER = _env("KB_RERANK_PROVIDER", "fastembed").lower()
RERANK_MODEL = _env("KB_RERANK_MODEL", "BAAI/bge-reranker-base")
RERANK_MIN_SCORE = _env_float("KB_RERANK_MIN_SCORE", 0.75)
RERANK_TRIGGER = _env_float("KB_RERANK_TRIGGER", 0.58)
RERANK_LONG_TRIGGER = _env_float("KB_RERANK_LONG_TRIGGER", 0.75)
RERANK_LONG_MIN_CHARS = _env_int("KB_RERANK_LONG_MIN_CHARS", 6)
RERANK_SHORTLIST = _env_int("KB_RERANK_SHORTLIST", 20)
# RERANK_LOCAL_FALLBACK is the legacy name; .env.example documents the KB_ one.
RERANK_LOCAL_FALLBACK = _env_bool(
    "KB_RERANK_LOCAL_FALLBACK", _env_bool("RERANK_LOCAL_FALLBACK", True)
)
RERANK_API_BASE = _env("RERANK_API_BASE").rstrip("/")
RERANK_API_KEY = _env("RERANK_API_KEY")
RERANK_API_MODEL = _env("RERANK_API_MODEL")
RERANK_API_PATH = _env("RERANK_API_PATH", "/rerank")
RERANK_API_HEADER = _env("RERANK_API_HEADER", "authorization").lower()
RERANK_API_USER_AGENT = _env(
    "RERANK_API_USER_AGENT",
    "Mozilla/5.0 (compatible; CUC-KnowledgeBase/0.1; +https://cuc.edu.cn)",
)
RERANK_API_TIMEOUT = _env_float("RERANK_API_TIMEOUT", 3)
# Same Bailian account serves rerank and embeddings unless overridden.
EMBED_API_KEY = EMBED_API_KEY or RERANK_API_KEY
# Rerank every query instead of only when the trigger heuristics fire.
RERANK_ALWAYS = _env_bool("KB_RERANK_ALWAYS", False)
# What to do when a rerank was needed but no reranker produced scores (cloud
# timeout/error and no local fallback). The un-reranked dense/BM25 floor
# accepts almost anything, so "refuse" marks the search no_answer; "keep" keeps
# the old floor decision. Either way the response carries degraded=true.
RERANK_FAILURE_POLICY = _env("KB_RERANK_FAILURE_POLICY", "refuse").lower()
if RERANK_FAILURE_POLICY not in ("refuse", "keep"):
    raise ValueError("KB_RERANK_FAILURE_POLICY must be 'refuse' or 'keep'")

# Ranking and answerability knobs (see search_core.py)
GENERIC_RERANK_PENALTY = _env_float("KB_GENERIC_RERANK_PENALTY", 0.10)
GENERIC_RRF_FACTOR = _env_float("KB_GENERIC_RRF_FACTOR", 0.85)
GENERIC_CLOSE_MARGIN = _env_float("KB_GENERIC_CLOSE_MARGIN", 0.10)
# Older editions of the same compilation stay searchable but yield to the
# newest edition when both are in the rerank shortlist.
VERSION_FAMILY_PENALTY = _env_float("KB_VERSION_FAMILY_PENALTY", 0.05)
# A doc explicitly superseded (废止) by a newer doc yields to it when both are
# candidates; the old doc stays fully searchable when the newer one is absent
# (e.g. "2022 年当时的规定" queries). Relation-driven, not a blanket date penalty.
SUPERSEDED_PENALTY = _env_float("KB_SUPERSEDED_PENALTY", 0.05)
CANDIDATE_POOL = _env_int("KB_CANDIDATE_POOL", 40)
CANDIDATE_LIMIT = _env_int("KB_CANDIDATE_LIMIT", 24)
MAX_CHUNKS_PER_DOC = _env_int("KB_MAX_CHUNKS_PER_DOC", 16)
# Post-retrieval context expansion: merge up to N adjacent chunks (same doc,
# active) into each top result's text, so premises/exceptions/consecutive
# clauses split across chunks are read together. 0 disables (default). Ranking
# and the answerability judgement are unchanged; only the returned text grows.
EXPAND_ADJACENT = _env_int("KB_EXPAND_ADJACENT", 0)
COVERAGE_BONUS = _env_float("KB_COVERAGE_BONUS", 0.004)
COVERAGE_MAX_BONUS = _env_float("KB_COVERAGE_MAX_BONUS", 0.03)
ANSWERABILITY_MAX_CONF = _env_float("KB_ANSWERABILITY_MAX_CONF", 0.95)
CONTENT_ANSWERABILITY_MAX_CONF = _env_float("KB_CONTENT_ANSWERABILITY_MAX_CONF", 0.97)
ANSWER_SPAN_WINDOW = _env_int("KB_ANSWER_SPAN_WINDOW", 50)
# When off, the answer-span check is reported in `answerability` but no longer
# overrides the rerank-threshold no_answer decision.
ANSWERABILITY_OVERRIDE = _env_bool("KB_ANSWERABILITY_OVERRIDE", True)

# OIDC login and browser session
OIDC_ISSUER = _env("KB_OIDC_ISSUER").rstrip("/")
OIDC_CLIENT_ID = _env("KB_OIDC_CLIENT_ID")
OIDC_CLIENT_SECRET = _env("KB_OIDC_CLIENT_SECRET")
OIDC_REDIRECT_URIS = _env_list("KB_OIDC_REDIRECT_URIS")
SESSION_SECRET = _env("KB_SESSION_SECRET")
SESSION_COOKIE = _env("KB_SESSION_COOKIE", "kb_session")
SESSION_TTL = _env_int("KB_SESSION_TTL", 43200)

# OCI object storage (private PDF bucket)
OCI_AUTH = _env("KB_OCI_AUTH", "instance_principal").lower()
OCI_REGION = _env("KB_OCI_REGION", "us-phoenix-1")
OCI_NAMESPACE = _env("KB_OCI_NAMESPACE", "axmb9dyegkxx")
OCI_BUCKET = _env("KB_OCI_BUCKET", "bucket-20261009-144522")
PAR_TTL = _env_int("KB_PAR_TTL", 900)

# Compilation/handbook documents answer many topics but cite poorly. They stay
# searchable, yet lose ranking weight when a specific regulation is also present.
GENERIC_TITLE_PREFIXES = (
    "中国传媒大学学生工作规章制度汇编",
    "参考：教师手册",
    "修学指南2025版",
    "QA知识导入模板",
    "2026年迎接新生开学相关工作",
    "学校部门分类",
)
# Compilations are also recognized when a year prefix/suffix precedes or
# follows the title, e.g. "2022年中国传媒大学学生工作规章制度汇编".
GENERIC_TITLE_MARKERS = (
    "学生工作规章制度汇编",
    "学生规章制度汇编",
)

_VERSION_LEAD_YEAR_RE = re.compile(r"^\s*(?:19|20)\d{2}\s*年?\s*")
_VERSION_TAIL_RE = re.compile(r"(?:19|20)\d{2}\s*(?:年)?\s*(?:[-—_]\s*转换之后)?\s*$")
_COMPILATION_MARKER = "规章制度汇编"


def is_generic_title(title):
    title = title or ""
    return any(title.startswith(prefix) for prefix in GENERIC_TITLE_PREFIXES) or any(
        marker in title for marker in GENERIC_TITLE_MARKERS
    )


def version_family(title):
    """Family key shared by editions of one compilation; None for other docs."""
    title = (title or "").strip()
    if _COMPILATION_MARKER not in title:
        return None
    family = _VERSION_TAIL_RE.sub("", _VERSION_LEAD_YEAR_RE.sub("", title)).strip(" -—_")
    return family or None


def edition_year(title):
    """Four-digit edition year from the title, or None when absent."""
    for match in re.finditer(r"(?:19|20)\d{2}", title or ""):
        year = int(match.group(0))
        if 2000 <= year <= 2099:
            return year
    return None


def qdrant_client(timeout=60):
    from qdrant_client import QdrantClient

    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None, timeout=timeout)
