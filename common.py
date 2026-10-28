import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("KB_DATA_DIR", str(BASE_DIR / "data")))
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
COLLECTION = os.getenv("KB_COLLECTION", "cuc_kb")
EMBED_MODEL = os.getenv("KB_EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
FASTEMBED_CACHE = Path(
    os.getenv("FASTEMBED_CACHE", str(BASE_DIR / ".cache/fastembed"))
)
RERANK_PROVIDER = os.getenv("KB_RERANK_PROVIDER", "fastembed").strip().lower()
RERANK_MODEL = os.getenv("KB_RERANK_MODEL", "BAAI/bge-reranker-base")
RERANK_MIN_SCORE = float(os.getenv("KB_RERANK_MIN_SCORE", "0.75"))
RERANK_TRIGGER = float(os.getenv("KB_RERANK_TRIGGER", "0.58"))
RERANK_LONG_TRIGGER = float(os.getenv("KB_RERANK_LONG_TRIGGER", "0.75"))
RERANK_LONG_MIN_CHARS = int(os.getenv("KB_RERANK_LONG_MIN_CHARS", "6"))
RERANK_SHORTLIST = int(os.getenv("KB_RERANK_SHORTLIST", "20"))
RERANK_API_BASE = os.getenv("RERANK_API_BASE", "").rstrip("/")
RERANK_API_KEY = os.getenv("RERANK_API_KEY", "")
RERANK_API_MODEL = os.getenv("RERANK_API_MODEL", "")
RERANK_API_PATH = os.getenv("RERANK_API_PATH", "/rerank")
RERANK_API_HEADER = os.getenv("RERANK_API_HEADER", "authorization").lower()
RERANK_API_USER_AGENT = os.getenv(
    "RERANK_API_USER_AGENT",
    "Mozilla/5.0 (compatible; CUC-KnowledgeBase/0.1; +https://cuc.edu.cn)",
)
RERANK_API_TIMEOUT = float(os.getenv("RERANK_API_TIMEOUT", "3"))
RERANK_LOCAL_FALLBACK = os.getenv("RERANK_LOCAL_FALLBACK", "1") == "1"
API_TOKEN = os.getenv("KB_API_TOKEN", "")
FILE_BASE_URL = os.getenv("KB_FILE_BASE_URL", "").rstrip("/")
FILE_OBJECT_PREFIX = os.getenv("KB_FILE_OBJECT_PREFIX", "cuc-kb/docs").strip("/")

PUBLIC_BASE_URL = os.getenv("KB_PUBLIC_BASE_URL", "").rstrip("/")
OIDC_ISSUER = os.getenv("KB_OIDC_ISSUER", "").rstrip("/")
OIDC_CLIENT_ID = os.getenv("KB_OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.getenv("KB_OIDC_CLIENT_SECRET", "")
OIDC_REDIRECT_URIS = [
    item.strip()
    for item in os.getenv("KB_OIDC_REDIRECT_URIS", "").split(",")
    if item.strip()
]
SESSION_SECRET = os.getenv("KB_SESSION_SECRET", "")
SESSION_COOKIE = os.getenv("KB_SESSION_COOKIE", "kb_session")
SESSION_TTL = int(os.getenv("KB_SESSION_TTL", "43200"))

OCI_AUTH = os.getenv("KB_OCI_AUTH", "instance_principal").strip().lower()
OCI_REGION = os.getenv("KB_OCI_REGION", "us-phoenix-1")
OCI_NAMESPACE = os.getenv("KB_OCI_NAMESPACE", "axmb9dyegkxx")
OCI_BUCKET = os.getenv("KB_OCI_BUCKET", "bucket-20261009-144522")
PAR_TTL = int(os.getenv("KB_PAR_TTL", "900"))

QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
