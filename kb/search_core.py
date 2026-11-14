import http.client
import json
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlsplit

import jieba
import numpy as np
from fastembed import TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue
from rank_bm25 import BM25Okapi

from kb.common import (
    ANSWER_SPAN_WINDOW,
    ANSWERABILITY_MAX_CONF,
    ANSWERABILITY_OVERRIDE,
    CANDIDATE_LIMIT,
    CANDIDATE_POOL,
    CLOUD_EMBED_PROVIDERS,
    CLOUD_RERANK_PROVIDERS,
    COLLECTION,
    CONTENT_ANSWERABILITY_MAX_CONF,
    COVERAGE_BONUS,
    COVERAGE_MAX_BONUS,
    DATA_DIR,
    EMBED_API_BASE,
    EMBED_API_KEY,
    EMBED_API_MODEL,
    EMBED_API_PATH,
    EMBED_API_TIMEOUT,
    EMBED_DIM,
    EMBED_MODEL,
    EMBED_PROVIDER,
    EMBED_QUERY_CACHE_SIZE,
    EMBED_QUERY_INSTRUCT,
    EXPAND_ADJACENT,
    FASTEMBED_CACHE,
    GENERIC_CLOSE_MARGIN,
    GENERIC_RERANK_PENALTY,
    GENERIC_RRF_FACTOR,
    MAX_CHUNKS_PER_DOC,
    PUBLIC_BASE_URL,
    QUERY_INSTRUCTION,
    RERANK_API_BASE,
    RERANK_API_HEADER,
    RERANK_API_KEY,
    RERANK_API_MODEL,
    RERANK_API_PATH,
    RERANK_API_TIMEOUT,
    RERANK_ALWAYS,
    RERANK_API_USER_AGENT,
    RERANK_FAILURE_POLICY,
    RERANK_LOCAL_FALLBACK,
    RERANK_LONG_MIN_CHARS,
    RERANK_LONG_TRIGGER,
    RERANK_MIN_SCORE,
    RERANK_MODEL,
    RERANK_PROVIDER,
    RERANK_SHORTLIST,
    RERANK_TRIGGER,
    SUPERSEDED_ADAPTIVE,
    SUPERSEDED_MARGIN,
    SUPERSEDED_PENALTY,
    VERSION_FAMILY_PENALTY,
    edition_year,
    is_generic_title,
    qdrant_client,
    version_family,
)

DOC_NO_RE = re.compile(r"[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*\d+\s*号")

DENSE_LIMIT = 40
BM25_LIMIT = 40
RRF_K = 60
DOC_NO_BONUS = 0.08
QA_BONUS = 0.004
NO_RERANK_MIN_DENSE = 0.45

INTENT_RERANK_HINTS = (
    "条件", "要求", "标准", "规定", "流程", "环节", "手续", "材料",
    "金额", "比例", "上限", "认定", "负责", "期限", "渠道", "方式",
    "依据", "定义", "几个", "等次", "多久", "年龄", "延期",
)
COVERAGE_STOPWORDS = {
    "哪些", "什么", "怎么", "如何", "需要", "满足", "可以", "是否",
    "进行", "相关", "多少", "以上", "以下", "一个", "以及", "或者",
    "其中", "必须", "应当", "完成", "提出", "对于", "关于", "这个",
}

ATTRIBUTE_HINTS = {
    "price": (
        "多少钱", "价格", "费用", "收费", "年卡", "月卡", "工资", "津贴",
        "奖学金", "补助", "报销", "标准", "金额", "学费", "住宿费", "罚款",
        "缴费", "自付", "自负",
    ),
    "time": (
        "几点", "什么时候", "何时", "时间", "期限", "多久", "年限", "学制",
        "年龄", "几岁", "提前", "日期", "工作日", "每周", "每天",
    ),
    "count": (
        "多少", "几人", "几个", "几条", "几篇", "几级", "多少票", "比例",
        "百分之", "名额", "人数", "学分", "分数", "第几", "数量",
    ),
    "channel": (
        "怎么", "如何", "渠道", "流程", "手续", "方式", "通过什么",
        "在哪里", "地点", "办理",
    ),
    "content": ("纪要", "名单", "目录", "菜单", "内容", "地址", "电话"),
    "person": ("谁", "哪个部门", "责任人", "负责人", "机构"),
}
# Attributes whose missing answer span forces no_answer; "channel" only hints.
STRICT_ATTRIBUTES = ("price", "time", "count", "content", "person")
SPAN_PATTERNS = {
    "price": re.compile(
        r"(?:\d+(?:\.\d+)?\s*(?:元|万元|块|元/|元每)|免费|不收费|"
        r"自负\d+|自付\d+|\d+%)"
    ),
    "time": re.compile(
        r"(?:\d+\s*(?:年|个月|月|天|日|小时|分钟|周|学期|岁|周岁|个工作日|工作日)|"
        r"\d{4}[./-]\d{1,2}[./-]\d{1,2}|每(?:学年|学期|年|月|周|天)|"
        r"[一二三四五六七八九十两]+(?:至[一二三四五六七八九十两]+)?\s*(?:年|个月|月|天|日|小时|周|学期|岁|周岁|个工作日|工作日))"
    ),
    "count": re.compile(
        r"(?:\d+\s*(?:人|名|个|条|篇|级|票|%|％|学分|分|次|档|项)|"
        r"[一二三四五六七八九十两]+(?:至[一二三四五六七八九十两]+)?\s*(?:人|名|个|条|篇|级|票|学分|分|次|档|项)|"
        r"三分之一|三分之二|半数|平均|不超过|不少于|至少|至多|I{1,3}V?\s*级)"
    ),
    "channel": re.compile(
        r"(?:电话|邮箱|网址|网站|公众号|APP|系统|平台|窗口|地点|办公室|"
        r"拨打|登录|申请|推荐|评审|考核|流程|手续|渠道)"
    ),
    "content": re.compile(
        r"(?:纪要|议题|名单|目录|菜单|内容|事项|决定|方案|http|www\.|电话|地址)"
    ),
    "person": re.compile(
        r"(?:负责|责任人|校长|院长|主任|委员会|领导小组|部门|处|办公室)"
    ),
}
ENTITY_STOPWORDS = COVERAGE_STOPWORDS | {
    "学生", "学校", "中国传媒大学", "老师", "教师", "研究生", "本科生",
    "北京市", "多少", "什么", "怎么", "如何", "哪些", "是否", "可以",
    "需要", "满足", "条件", "要求", "标准", "规定", "内容", "时间",
    "期限", "方式", "渠道", "负责", "办理", "申请", "进行", "相关",
    "以上", "以下", "个人", "单位", "部门", "一次", "要交", "教职",
    "教职工", "职工",
}
TEMPORAL_HINTS = ("今天", "今日", "本周", "本月", "今年")
DATE_RE = re.compile(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}|\d{1,2}月\d{1,2}日")
ORDINAL_RE = re.compile(r"第\d+[次条章节]|\d+次")


def detect_attribute(query):
    for attribute, hints in ATTRIBUTE_HINTS.items():
        if any(hint in query for hint in hints):
            return attribute
    return None


def significant_terms(query, stopwords):
    return {
        term
        for term in jieba.lcut_for_search(query)
        if len(term) >= 2 and term not in stopwords
    }


def query_entities(query):
    return significant_terms(query, ENTITY_STOPWORDS)


def has_answer_span(text, attribute, entities, query=""):
    if not entities:
        return False
    temporal = next((item for item in TEMPORAL_HINTS if item in query), None)
    ordinals = ORDINAL_RE.findall(query)
    for match in SPAN_PATTERNS[attribute].finditer(text):
        window = text[
            max(0, match.start() - ANSWER_SPAN_WINDOW) :
            match.end() + ANSWER_SPAN_WINDOW
        ]
        if not any(entity in window for entity in entities):
            continue
        if ordinals and not any(item in window for item in ordinals):
            continue
        if temporal and not (temporal in window or DATE_RE.search(window)):
            continue
        return True
    return False


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def is_generic_chunk(chunk) -> bool:
    if not chunk:
        return False
    return bool(chunk.get("is_generic")) or is_generic_title(chunk.get("doc_title", ""))


def rrf(rank):
    return 1.0 / (RRF_K + rank + 1)


def best_split(items, is_generic, score):
    """Best score among generic items and among specific items (None when absent)."""
    generic_best = specific_best = None
    for item in items:
        value = score(item)
        if is_generic(item):
            generic_best = value if generic_best is None else max(generic_best, value)
        else:
            specific_best = value if specific_best is None else max(specific_best, value)
    return generic_best, specific_best


class CloudEmbedder:
    """Query-side encoder against an OpenAI-compatible /embeddings endpoint.

    Mirrors CloudReranker's per-thread keep-alive connection: a fresh TCP+TLS
    handshake to Beijing adds ~0.7 s per query. Documents are embedded offline
    via scripts/batch_embed.py with the same model/dimension and no instruct.
    """

    def __init__(self, base, path, model, api_key, dim, instruct, timeout):
        self.url = base.rstrip("/") + "/" + path.lstrip("/")
        self.model = model
        self.api_key = api_key
        self.dim = dim
        self.instruct = instruct
        self.timeout = timeout
        self._local = threading.local()

    @classmethod
    def from_env(cls):
        return cls(
            base=EMBED_API_BASE,
            path=EMBED_API_PATH,
            model=EMBED_API_MODEL,
            api_key=EMBED_API_KEY,
            dim=EMBED_DIM,
            instruct=EMBED_QUERY_INSTRUCT,
            timeout=EMBED_API_TIMEOUT,
        )

    def _connection(self, parts):
        conn = getattr(self._local, "conn", None)
        if conn is None or getattr(self._local, "netloc", None) != parts.netloc:
            if conn is not None:
                conn.close()
            factory = (
                http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
            )
            conn = factory(parts.netloc, timeout=self.timeout)
            self._local.conn, self._local.netloc = conn, parts.netloc
        return conn

    def _drop_connection(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
        self._local.conn = None

    def embed(self, query: str):
        payload = {
            "model": self.model,
            "input": query,
            "dimensions": self.dim,
            "encoding_format": "float",
        }
        if self.instruct:
            payload["instruct"] = self.instruct
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": RERANK_API_USER_AGENT,
        }
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        parts = urlsplit(self.url)
        path = parts.path + ("?" + parts.query if parts.query else "")
        data = json.dumps(payload).encode("utf-8")
        for attempt in range(2):
            conn = self._connection(parts)
            reused = conn.sock is not None
            try:
                conn.request("POST", path, body=data, headers=headers)
                response = conn.getresponse()
                body = response.read()
            except TimeoutError:
                self._drop_connection()
                raise
            except (http.client.HTTPException, OSError):
                self._drop_connection()
                if reused and attempt == 0:
                    continue
                raise
            if response.will_close:
                self._drop_connection()
            if response.status >= 400:
                detail = body.decode("utf-8", "ignore")[:300]
                raise RuntimeError(f"HTTP {response.status}: {detail}")
            parsed = json.loads(body.decode("utf-8"))
            vector = np.asarray(parsed["data"][0]["embedding"], dtype=np.float32)
            if vector.shape[0] != self.dim:
                raise RuntimeError(
                    f"embedding dim {vector.shape[0]} != KB_EMBED_DIM {self.dim}"
                )
            return vector / max(float(np.linalg.norm(vector)), 1e-12)
        raise RuntimeError("embedding request failed after reconnect")


class CloudReranker:
    def __init__(self, base, path, model, api_key, header, timeout):
        self.url = base.rstrip("/") + "/" + path.lstrip("/")
        self.model = model
        self.api_key = api_key
        self.header = header
        self.timeout = timeout
        self._local = threading.local()

    @classmethod
    def from_env(cls):
        return cls(
            base=RERANK_API_BASE,
            path=RERANK_API_PATH,
            model=RERANK_API_MODEL,
            api_key=RERANK_API_KEY,
            header=RERANK_API_HEADER,
            timeout=RERANK_API_TIMEOUT,
        )

    def _post(self, payload):
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": RERANK_API_USER_AGENT,
        }
        if self.api_key:
            if self.header == "api-key":
                headers["api-key"] = self.api_key
            else:
                headers["Authorization"] = "Bearer " + self.api_key
        parts = urlsplit(self.url)
        path = parts.path + ("?" + parts.query if parts.query else "")
        data = json.dumps(payload).encode("utf-8")
        # One keep-alive connection per worker thread: a fresh TCP+TLS handshake
        # to the Beijing endpoint adds ~0.7 s and causes most of the >4 s tail.
        for attempt in range(2):
            conn = self._connection(parts)
            reused = conn.sock is not None
            try:
                conn.request("POST", path, body=data, headers=headers)
                response = conn.getresponse()
                body = response.read()
            except TimeoutError:
                self._drop_connection()
                raise  # a retry would double the wait; caller degrades instead
            except (http.client.HTTPException, OSError):
                self._drop_connection()
                if reused and attempt == 0:
                    continue  # server closed the idle connection; reconnect once
                raise
            if response.will_close:
                self._drop_connection()
            if response.status >= 400:
                detail = body.decode("utf-8", "ignore")[:300]
                raise RuntimeError(f"HTTP {response.status}: {detail}")
            return json.loads(body.decode("utf-8"))

    def _connection(self, parts):
        conn = getattr(self._local, "conn", None)
        if conn is None or getattr(self._local, "netloc", None) != parts.netloc:
            if conn is not None:
                conn.close()
            factory = (
                http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
            )
            conn = factory(parts.netloc, timeout=self.timeout)
            self._local.conn, self._local.netloc = conn, parts.netloc
        return conn

    def _drop_connection(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
        self._local.conn = None

    def rerank(self, query, documents):
        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
        }
        try:
            body = self._post(payload)
        except RuntimeError as exc:
            # Some providers reject top_n; retry once without it.
            if "HTTP 400" not in str(exc):
                raise
            payload.pop("top_n", None)
            body = self._post(payload)

        items = body.get("results") or body.get("data") or []
        if not isinstance(items, list):
            raise RuntimeError("unexpected rerank response")
        scores = [None] * len(documents)
        for item in items:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            score = item.get("relevance_score", item.get("score"))
            if index is None or score is None:
                continue
            index = int(index)
            if 0 <= index < len(scores):
                scores[index] = float(score)
        if all(score is None for score in scores):
            # No usable indices: assume results come back in input order.
            for index, item in enumerate(items[: len(scores)]):
                if isinstance(item, dict):
                    score = item.get("relevance_score", item.get("score"))
                    if score is not None:
                        scores[index] = float(score)
        if all(score is None for score in scores):
            raise RuntimeError(
                "no scores in rerank response: "
                + json.dumps(body, ensure_ascii=False)[:300]
            )
        return scores


def _local_reranker(label):
    try:
        return TextCrossEncoder(model_name=RERANK_MODEL, cache_dir=str(FASTEMBED_CACHE))
    except Exception as exc:
        print(f"{label} unavailable: {exc}", flush=True)
        return None


class KB:
    def __init__(self):
        self.chunks = []
        self.chunk_map = {}
        self.doc_chunks = {}
        self.doc_meta = {}
        self.page_counts = {}
        self.bm25 = None
        self.model = None
        self.cloud_embedder = None
        self.reranker = None
        self.cloud_reranker = None
        self.client = None
        self.lock = threading.Lock()
        self.loaded_at = None
        self.bm25_postings = None
        self.bm25_doc_len = None
        self.bm25_masks = None
        self._embed_cache = OrderedDict()
        self._embed_cache_lock = threading.Lock()

    # ---- loading -----------------------------------------------------------

    def load(self):
        started = time.time()
        self._load_chunks()
        self._load_doc_meta()

        tokenized = [jieba.lcut_for_search(chunk["embed_text"]) for chunk in self.chunks]
        self.bm25 = BM25Okapi(tokenized) if tokenized else None
        if self.bm25:
            self._build_bm25_index()

        if EMBED_PROVIDER in CLOUD_EMBED_PROVIDERS:
            if not EMBED_API_KEY:
                raise RuntimeError("KB_EMBED_PROVIDER=cloud requires EMBED_API_KEY (or RERANK_API_KEY)")
            self.cloud_embedder = CloudEmbedder.from_env()
            print(
                f"cloud embedder: {self.cloud_embedder.url} model={EMBED_API_MODEL} dim={EMBED_DIM}",
                flush=True,
            )
        else:
            self.model = TextEmbedding(model_name=EMBED_MODEL, cache_dir=str(FASTEMBED_CACHE))
        self._load_rerankers()
        self.client = qdrant_client(timeout=60)
        self.loaded_at = time.time()
        print(
            f"KB loaded: {len(self.chunks)} chunks in {self.loaded_at - started:.1f}s",
            flush=True,
        )

    def _load_chunks(self):
        chunks_path = DATA_DIR / "chunks.jsonl"
        if not chunks_path.exists():
            raise RuntimeError(f"missing {chunks_path}, run ingest first")
        with chunks_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    self._add_chunk(json.loads(line))

    def _add_chunk(self, chunk):
        self.chunks.append(chunk)
        self.chunk_map[chunk["chunk_id"]] = chunk
        self.doc_chunks.setdefault(chunk["doc_id"], []).append(chunk)

    def _load_doc_meta(self):
        docs_path = DATA_DIR / "docs.json"
        if docs_path.exists():
            try:
                for doc in json.loads(docs_path.read_text(encoding="utf-8")):
                    if doc.get("doc_id"):
                        self.doc_meta[doc["doc_id"]] = doc
            except (OSError, json.JSONDecodeError) as exc:
                print(f"cannot load {docs_path}: {exc}", flush=True)
        self.page_counts = {
            doc_id: int(doc.get("pages") or 0) for doc_id, doc in self.doc_meta.items()
        }
        for chunk in self.chunks:
            doc_id = chunk["doc_id"]
            self.page_counts[doc_id] = max(
                self.page_counts.get(doc_id, 0), int(chunk.get("page_end") or 0)
            )

    def _load_rerankers(self):
        if RERANK_PROVIDER in CLOUD_RERANK_PROVIDERS:
            if not RERANK_API_BASE or not RERANK_API_MODEL:
                raise RuntimeError(
                    "KB_RERANK_PROVIDER=openai requires RERANK_API_BASE and RERANK_API_MODEL"
                )
            self.cloud_reranker = CloudReranker.from_env()
            print(
                f"cloud reranker: {self.cloud_reranker.url} model={RERANK_API_MODEL}",
                flush=True,
            )
            if RERANK_LOCAL_FALLBACK and RERANK_MODEL:
                self.reranker = _local_reranker("local rerank fallback")
        elif RERANK_PROVIDER == "fastembed" and RERANK_MODEL:
            self.reranker = _local_reranker("reranker")

    # ---- helpers -----------------------------------------------------------

    def encode_query(self, query: str):
        with self._embed_cache_lock:
            cached = self._embed_cache.get(query)
            if cached is not None:
                self._embed_cache.move_to_end(query)
                return cached
        if self.cloud_embedder is not None:
            vector = self.cloud_embedder.embed(query)
        else:
            with self.lock:
                try:
                    vector = list(self.model.query_embed(query))[0]
                except AttributeError:
                    vector = list(self.model.embed([QUERY_INSTRUCTION + query]))[0]
            vector = np.asarray(vector, dtype=np.float32)
            vector = vector / max(float(np.linalg.norm(vector)), 1e-12)
        if EMBED_QUERY_CACHE_SIZE > 0:
            with self._embed_cache_lock:
                self._embed_cache[query] = vector
                if len(self._embed_cache) > EMBED_QUERY_CACHE_SIZE:
                    self._embed_cache.popitem(last=False)
        return vector

    def _filter(self, assistant, doc_types):
        conditions = [FieldCondition(key="status", match=MatchValue(value="active"))]
        if assistant:
            conditions.append(
                FieldCondition(key="assistants", match=MatchAny(any=[assistant, "shared"]))
            )
        if doc_types:
            conditions.append(
                FieldCondition(key="doc_type", match=MatchAny(any=list(doc_types)))
            )
        return Filter(must=conditions)

    def _matches(self, chunk, assistant, doc_types):
        if chunk.get("status") != "active":
            return False
        if assistant:
            assistants = chunk.get("assistants") or []
            if assistant not in assistants and "shared" not in assistants:
                return False
        if doc_types and chunk.get("doc_type") not in doc_types:
            return False
        return True

    def _is_generic_id(self, chunk_id):
        return is_generic_chunk(self.chunk_map.get(chunk_id))

    def file_url(self, doc_id, page=None):
        if not doc_id or doc_id == "qa-csv":
            return ""
        url = f"{PUBLIC_BASE_URL}/v1/files/{quote(doc_id, safe='')}"
        if page:
            url += f"#page={int(page)}"
        return url

    def page_count(self, doc_id):
        return int(self.page_counts.get(doc_id, 0))

    # ---- retrieval stages --------------------------------------------------

    def dense_search(self, vector, query_filter, limit):
        try:
            response = self.client.query_points(
                collection_name=COLLECTION,
                query=vector.tolist(),
                using="dense",
                query_filter=query_filter,
                limit=limit,
                # Only chunk_id is consumed; fetching full payloads (chunk text
                # included) wastes serialization on every query.
                with_payload=["chunk_id"],
            )
        except Exception as exc:
            print(f"qdrant search failed: {exc}", flush=True)
            return []
        hits = []
        for point in response.points:
            chunk_id = (point.payload or {}).get("chunk_id")
            if chunk_id:
                hits.append((chunk_id, float(point.score)))
        return hits

    def bm25_search(self, query, limit, assistant, doc_types):
        if not self.bm25:
            return []
        if self.bm25_postings is None:
            # Manually assembled KB (tests): build the index on first use.
            with self.lock:
                if self.bm25_postings is None:
                    self._build_bm25_index()
        scores = self._bm25_scores(jieba.lcut_for_search(query))
        mask = self.bm25_masks["active"]
        if assistant:
            assistant_mask = self.bm25_masks["assistants"].get(assistant)
            shared_mask = self.bm25_masks["assistants"]["shared"]
            mask = mask & (shared_mask if assistant_mask is None else assistant_mask | shared_mask)
        if doc_types:
            type_mask = self.bm25_masks["empty"]
            for doc_type in doc_types:
                found = self.bm25_masks["doc_types"].get(doc_type)
                if found is not None:
                    type_mask = type_mask | found
            mask = mask & type_mask
        indexes = np.flatnonzero(mask & (scores > 0))
        if indexes.size == 0:
            return []
        # Stable sort keeps index order among equal scores, matching the
        # original append-then-sort behaviour exactly.
        indexes = indexes[np.argsort(-scores[indexes], kind="stable")][:limit]
        return [(self.chunks[index]["chunk_id"], float(scores[index])) for index in indexes]

    def _build_bm25_index(self):
        """Sparse postings + filter masks over the BM25 corpus.

        rank_bm25's get_scores scans all 52k doc Counters per query term
        (~100-200 ms/query). Scoring only the documents that contain each term
        is mathematically identical: a zero frequency contributes exactly 0.0,
        and per-document additions keep the original query-term order.
        """
        postings = {}
        for index, freqs in enumerate(self.bm25.doc_freqs):
            for term, freq in freqs.items():
                entry = postings.get(term)
                if entry is None:
                    postings[term] = ([index], [freq])
                else:
                    entry[0].append(index)
                    entry[1].append(freq)
        self.bm25_postings = {
            term: (np.asarray(indexes, dtype=np.int64), np.asarray(freqs, dtype=np.int64))
            for term, (indexes, freqs) in postings.items()
        }
        self.bm25_doc_len = np.asarray(self.bm25.doc_len)
        size = len(self.chunks)
        assistants = sorted(
            {
                assistant
                for chunk in self.chunks
                for assistant in (chunk.get("assistants") or [])
            }
        )
        doc_types = sorted({chunk.get("doc_type") for chunk in self.chunks if chunk.get("doc_type")})
        self.bm25_masks = {
            "active": np.fromiter(
                (chunk.get("status") == "active" for chunk in self.chunks),
                dtype=bool, count=size,
            ),
            "empty": np.zeros(size, dtype=bool),
            "assistants": {
                assistant: np.fromiter(
                    (assistant in (chunk.get("assistants") or []) for chunk in self.chunks),
                    dtype=bool, count=size,
                )
                for assistant in assistants
            },
            "doc_types": {
                doc_type: np.fromiter(
                    (chunk.get("doc_type") == doc_type for chunk in self.chunks),
                    dtype=bool, count=size,
                )
                for doc_type in doc_types
            },
        }
        if "shared" not in self.bm25_masks["assistants"]:
            self.bm25_masks["assistants"]["shared"] = self.bm25_masks["empty"]

    def _bm25_scores(self, tokens):
        """Bit-identical to BM25Okapi.get_scores, restricted to term postings."""
        bm25 = self.bm25
        scores = np.zeros(bm25.corpus_size)
        doc_len = self.bm25_doc_len
        for term in tokens:
            idf = bm25.idf.get(term) or 0
            posting = self.bm25_postings.get(term)
            if posting is None or not idf:
                continue
            indexes, freqs = posting
            scores[indexes] += idf * (
                freqs * (bm25.k1 + 1)
                / (freqs + bm25.k1 * (1 - bm25.b + bm25.b * doc_len[indexes] / bm25.avgdl))
            )
        return scores

    def _apply_fusion_bonuses(self, query, fused):
        doc_no_match = DOC_NO_RE.search(query)
        target_doc_no = normalize(doc_no_match.group(0)) if doc_no_match else ""
        query_terms = significant_terms(query, COVERAGE_STOPWORDS)
        for chunk_id in fused:
            chunk = self.chunk_map.get(chunk_id)
            if not chunk:
                continue
            if target_doc_no and target_doc_no in normalize(chunk.get("doc_no", "")):
                fused[chunk_id] += DOC_NO_BONUS
            if chunk.get("is_qa"):
                fused[chunk_id] += QA_BONUS
            if query_terms and not is_generic_chunk(chunk):
                text = chunk.get("text", "")
                coverage = sum(1 for term in query_terms if term in text)
                if coverage:
                    fused[chunk_id] += min(COVERAGE_BONUS * coverage, COVERAGE_MAX_BONUS)

    def _select_candidates(self, fused):
        """Top fused items, capped per document."""
        ranked = sorted(fused.items(), key=lambda item: item[1], reverse=True)
        per_doc = {}
        candidates = []
        for item in ranked[:CANDIDATE_POOL]:
            chunk = self.chunk_map.get(item[0])
            if not chunk:
                continue
            doc_id = chunk.get("doc_id")
            if per_doc.get(doc_id, 0) >= MAX_CHUNKS_PER_DOC:
                continue
            candidates.append(item)
            per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
            if len(candidates) >= CANDIDATE_LIMIT:
                break
        return candidates

    def _should_rerank(self, query, best_dense):
        if RERANK_ALWAYS:
            return True
        if any(hint in query for hint in INTENT_RERANK_HINTS):
            return True
        if best_dense < RERANK_TRIGGER:
            return True
        return len(query) >= RERANK_LONG_MIN_CHARS and best_dense < RERANK_LONG_TRIGGER

    def _rerank_scores(self, query, documents):
        """Cloud rerank with local fallback; None when no reranker produced scores."""
        if self.cloud_reranker:
            try:
                return self.cloud_reranker.rerank(query, documents)
            except Exception as exc:
                print(f"cloud rerank failed: {exc}", flush=True)
                label = "local rerank fallback"
        else:
            label = "rerank"
        if self.reranker:
            try:
                # fastembed returns raw logits; map them onto the cloud providers'
                # 0-1 scale so thresholds and bonuses keep their meaning.
                logits = np.asarray(list(self.reranker.rerank(query, documents)), dtype=np.float64)
                return (1.0 / (1.0 + np.exp(-logits))).tolist()
            except Exception as exc:
                print(f"{label} failed: {exc}", flush=True)
        return None

    def _adjusted_rerank_score(self, chunk, score, generic_penalty):
        if generic_penalty and is_generic_chunk(chunk):
            return score - GENERIC_RERANK_PENALTY
        return score

    def _newest_editions(self, items):
        """Newest edition year per compilation family among the candidates."""
        newest = {}
        for item in items:
            chunk_id = item[0]
            chunk = self.chunk_map.get(chunk_id) or {}
            if not is_generic_chunk(chunk):
                continue
            family = version_family(chunk.get("doc_title", ""))
            year = edition_year(chunk.get("doc_title", ""))
            if not family or year is None:
                continue
            newest[family] = max(year, newest.get(family, year))
        return newest

    def _is_older_edition(self, chunk, newest):
        if not is_generic_chunk(chunk):
            return False
        family = version_family(chunk.get("doc_title", ""))
        year = edition_year(chunk.get("doc_title", ""))
        return family in newest and year is not None and year < newest[family]

    def _superseded_relations(self, items):
        """{old_doc_id: new_doc_id} for superseded docs whose replacement is a candidate.

        Relation-driven (chunk.superseded_by): an old version only counts as
        superseded here when its replacement is also among the candidates, so
        temporal queries ("2022 年当时的规定") still retrieve and expand it.
        """
        present = set()
        for chunk_id, _ in items:
            chunk = self.chunk_map.get(chunk_id)
            if chunk:
                present.add(chunk.get("doc_id"))
        relations = {}
        for chunk_id, _ in items:
            chunk = self.chunk_map.get(chunk_id) or {}
            target = chunk.get("superseded_by")
            old = chunk.get("doc_id")
            if target and target in present and target != old:
                relations[old] = target
        return relations

    def _superseded_penalties(self, items, score_of):
        """Per-old-doc demotion when its replacement is also a candidate.

        Returns {old_doc_id: penalty}. The base is SUPERSEDED_PENALTY; with
        SUPERSEDED_ADAPTIVE the penalty grows to put the old doc's best chunk a
        margin below the replacement's best chunk, so a large rerank gap cannot
        keep a 废止d version on top. ``score_of`` maps chunk_id -> the score the
        ordering uses *before* the superseded demotion (generic/edition already
        applied), or None when the chunk was not scored. Relation-driven: an old
        doc with no replacement among the candidates is not demoted at all.
        """
        relations = self._superseded_relations(items)
        if not relations:
            return {}
        present = {}
        for chunk_id, _ in items:
            chunk = self.chunk_map.get(chunk_id)
            if chunk:
                present.setdefault(chunk.get("doc_id"), []).append(chunk_id)
        best = {}
        for doc_id, chunk_ids in present.items():
            scores = [score_of(chunk_id) for chunk_id in chunk_ids]
            scores = [score for score in scores if score is not None]
            if scores:
                best[doc_id] = max(scores)
        penalties = {}
        for old, new in relations.items():
            penalty = SUPERSEDED_PENALTY
            if SUPERSEDED_ADAPTIVE and old in best and new in best:
                penalty = max(penalty, best[old] - best[new] + SUPERSEDED_MARGIN)
            penalties[old] = penalty
        return penalties

    def _rerank(self, query, candidates):
        """Return (reordered candidates, rerank scores by chunk_id)."""
        shortlist = candidates[: max(RERANK_SHORTLIST, CANDIDATE_LIMIT)]
        documents = [self.chunk_map[chunk_id]["embed_text"] for chunk_id, _ in shortlist]
        scores = self._rerank_scores(query, documents)
        if not scores:
            return candidates, {}

        rerank_scores = {
            chunk_id: float(score)
            for (chunk_id, _), score in zip(shortlist, scores)
            if score is not None
        }
        reranked = [item for item in shortlist if item[0] in rerank_scores]
        generic_best, specific_best = best_split(
            reranked,
            lambda item: self._is_generic_id(item[0]),
            lambda item: rerank_scores[item[0]],
        )
        generic_penalty = (
            generic_best is not None
            and specific_best is not None
            and specific_best >= generic_best - GENERIC_CLOSE_MARGIN
        )
        newest = self._newest_editions(reranked)
        pre = {}
        for item in reranked:
            chunk = self.chunk_map.get(item[0]) or {}
            score = self._adjusted_rerank_score(chunk, rerank_scores[item[0]], generic_penalty)
            if self._is_older_edition(chunk, newest):
                score -= VERSION_FAMILY_PENALTY
            pre[item[0]] = score
        penalties = self._superseded_penalties(reranked, pre.get)

        def adjusted(item):
            chunk = self.chunk_map.get(item[0]) or {}
            return pre[item[0]] - penalties.get(chunk.get("doc_id"), 0.0)

        reranked.sort(key=adjusted, reverse=True)
        others = [item for item in candidates if item[0] not in rerank_scores]
        return reranked + others, rerank_scores

    def _demote_generic_rrf(self, candidates):
        """Without rerank, down-weight generic compilations and older editions."""
        generic_best, specific_best = best_split(
            candidates, lambda item: self._is_generic_id(item[0]), lambda item: item[1]
        )
        generic_penalty = (
            generic_best is not None
            and specific_best is not None
            and specific_best >= generic_best * GENERIC_RRF_FACTOR
        )
        newest = self._newest_editions(candidates)
        pre = {}
        for item in candidates:
            chunk = self.chunk_map.get(item[0]) or {}
            score = item[1]
            if generic_penalty and self._is_generic_id(item[0]):
                score *= GENERIC_RRF_FACTOR
            if self._is_older_edition(chunk, newest):
                score -= VERSION_FAMILY_PENALTY
            pre[item[0]] = score
        penalties = self._superseded_penalties(candidates, pre.get)
        if not generic_penalty and not newest and not penalties:
            return

        def adjusted(item):
            chunk = self.chunk_map.get(item[0]) or {}
            return pre[item[0]] - penalties.get(chunk.get("doc_id"), 0.0)

        candidates.sort(key=adjusted, reverse=True)

    def _citation(self, chunk):
        return {
            "doc_title": chunk.get("doc_title", ""),
            "doc_no": chunk.get("doc_no", ""),
            "doc_type": chunk.get("doc_type", ""),
            "clause_no": chunk.get("clause_no", ""),
            "page_start": chunk.get("page_start", 0),
            "page_end": chunk.get("page_end", 0),
            "section_path": chunk.get("section_path", []),
            "source_file": chunk.get("source_file", ""),
            "source_url": chunk.get("source_url", ""),
            "publish_date": chunk.get("publish_date", ""),
            "date_basis": chunk.get("date_basis", ""),
            "superseded_by": chunk.get("superseded_by", ""),
            "page_count": self.page_count(chunk["doc_id"]),
            "file_url": self.file_url(chunk["doc_id"], chunk.get("page_start") or None),
        }

    def _expand_adjacent(self, result, n):
        """Merge up to n active neighbour chunks into a result's text (read aid).

        Neighbours are the document-order chunks at sequence ±1..n of the anchor
        (the chunk_id suffix). Ranking and scores are untouched; the citation
        page range widens to the merged span and expanded_chunks lists added ids.
        """
        chunk_id = result["chunk_id"]
        doc_id = result["doc_id"]
        prefix, sep, seq = chunk_id.rpartition("-")
        if not sep or prefix != doc_id or not seq.isdigit():
            return
        anchor = self.chunk_map.get(chunk_id)
        if not anchor:
            return
        center = int(seq)
        parts = [(center, anchor)]
        for offset in range(1, n + 1):
            for signed in (-offset, offset):
                neighbor = self.chunk_map.get(f"{doc_id}-{center + signed:04d}")
                if neighbor and neighbor.get("status") == "active":
                    parts.append((center + signed, neighbor))
        if len(parts) == 1:
            return
        parts.sort(key=lambda item: item[0])
        result["text"] = "\n".join(chunk.get("text", "") for _, chunk in parts)
        result["expanded_chunks"] = [c["chunk_id"] for _, c in parts if c["chunk_id"] != chunk_id]
        result["expanded"] = True
        starts = [int(c.get("page_start") or 0) for _, c in parts]
        ends = [int(c.get("page_end") or 0) for _, c in parts]
        citation = result["citation"]
        positive_starts = [s for s in starts if s > 0]
        if positive_starts:
            citation["page_start"] = min(positive_starts)
        citation["page_end"] = max(ends)

    def _judge_answer(self, query, results):
        """Return (no_answer, confidence, answerability) for the final results."""
        best_dense = max((item["dense_score"] for item in results), default=0.0)
        best_bm25 = max((item["bm25_score"] for item in results), default=0.0)
        best_rerank = max(
            (item["rerank_score"] for item in results if item["rerank_score"] is not None),
            default=None,
        )
        if best_rerank is not None:
            no_answer = best_rerank < RERANK_MIN_SCORE
            confidence = best_rerank
        else:
            no_answer = not results or (best_dense < NO_RERANK_MIN_DENSE and best_bm25 <= 0.0)
            confidence = best_dense

        attribute = detect_attribute(query) if results else None
        if not attribute:
            return no_answer, confidence, None
        limit = (
            CONTENT_ANSWERABILITY_MAX_CONF if attribute == "content" else ANSWERABILITY_MAX_CONF
        )
        if confidence >= limit:
            return no_answer, confidence, None

        entities = query_entities(query)
        answerable = any(
            has_answer_span(item["text"], attribute, entities, query) for item in results[:5]
        )
        if ANSWERABILITY_OVERRIDE:
            if answerable:
                no_answer = False
            elif attribute in STRICT_ATTRIBUTES:
                no_answer = True
        return no_answer, confidence, {"attribute": attribute, "answerable": answerable}

    # ---- public API --------------------------------------------------------

    def search(self, query, assistant=None, doc_types=None, top_k=5, trace=False):
        started = time.time()
        query = (query or "").strip()
        if not query:
            return {"query": query, "results": [], "no_answer": True, "took_ms": 0}

        # BM25 is independent of the query vector: run it while the cloud
        # embedding round-trip and the Qdrant lookup happen on this thread.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="kb-bm25") as pool:
            bm25_future = pool.submit(self.bm25_search, query, BM25_LIMIT, assistant, doc_types)
            vector = self.encode_query(query)
            dense_hits = self.dense_search(vector, self._filter(assistant, doc_types), DENSE_LIMIT)
            bm25_hits = bm25_future.result()

        dense_scores = dict(dense_hits)
        bm25_scores = dict(bm25_hits)
        fused = {}
        for hits in (dense_hits, bm25_hits):
            for rank, (chunk_id, _) in enumerate(hits):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + rrf(rank)
        self._apply_fusion_bonuses(query, fused)

        candidates = self._select_candidates(fused)
        selected_ids = [chunk_id for chunk_id, _ in candidates] if trace else []
        rerank_scores = {}
        rerank_failed = False
        rerank_triggered = False
        best_dense_all = max(dense_scores.values(), default=0.0)
        if (
            (self.cloud_reranker or self.reranker)
            and candidates
            and self._should_rerank(query, best_dense_all)
        ):
            rerank_triggered = True
            candidates, rerank_scores = self._rerank(query, candidates)
            # The rerank was needed but nothing scored: the hybrid floor below
            # is not calibrated for this, so flag the response as degraded.
            rerank_failed = not rerank_scores
        if not rerank_scores and candidates:
            self._demote_generic_rrf(candidates)

        results = []
        for chunk_id, score in candidates[:top_k]:
            chunk = self.chunk_map.get(chunk_id)
            if not chunk:
                continue
            rerank_score = rerank_scores.get(chunk_id)
            results.append(
                {
                    "chunk_id": chunk_id,
                    "doc_id": chunk["doc_id"],
                    "text": chunk["text"],
                    "score": round(score, 6),
                    "dense_score": round(dense_scores.get(chunk_id, 0.0), 4),
                    "bm25_score": round(bm25_scores.get(chunk_id, 0.0), 4),
                    "rerank_score": None if rerank_score is None else round(rerank_score, 4),
                    "citation": self._citation(chunk),
                    "is_qa": chunk.get("is_qa", False),
                    "is_generic": is_generic_chunk(chunk),
                }
            )

        no_answer, confidence, answerability = self._judge_answer(query, results)
        if rerank_failed and RERANK_FAILURE_POLICY == "refuse":
            no_answer = True
        if EXPAND_ADJACENT > 0:
            # Do not expand a superseded old version when its replacement is in
            # play: expansion would re-merge the very obsolete clauses the
            # demotion pushed down (e.g. a 废止d "每年4-5月" clause). Temporal
            # queries keep expansion because the replacement is then absent.
            skip_expand = (
                set(self._superseded_relations(candidates)) if SUPERSEDED_ADAPTIVE else set()
            )
            for item in results:
                if item["doc_id"] in skip_expand:
                    continue
                self._expand_adjacent(item, EXPAND_ADJACENT)
        payload = {
            "query": query,
            "results": results,
            "no_answer": no_answer,
            "confidence": round(float(confidence), 4),
            "answerability": answerability,
            "degraded": rerank_failed,
            "degraded_reason": "rerank_unavailable" if rerank_failed else None,
            "took_ms": int((time.time() - started) * 1000),
        }
        if trace:
            # Opt-in stage snapshot for eval diagnostics (eval/evidence_check.py);
            # never requested by the agent-facing tools, zero cost when off.
            payload["trace"] = {
                "dense": [{"chunk_id": c, "score": round(s, 4)} for c, s in dense_hits],
                "bm25": [{"chunk_id": c, "score": round(s, 4)} for c, s in bm25_hits],
                "fused": [
                    {"chunk_id": c, "score": round(s, 6)}
                    for c, s in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:CANDIDATE_POOL]
                ],
                "selected": selected_ids,
                "rerank_triggered": rerank_triggered,
                "rerank_failed": rerank_failed,
                "rerank_scores": {c: round(s, 4) for c, s in rerank_scores.items()},
                "order": [chunk_id for chunk_id, _ in candidates],
                "final": [item["chunk_id"] for item in results],
            }
        return payload

    def meta(self):
        assistants = sorted(
            {
                assistant
                for chunk in self.chunks
                for assistant in (chunk.get("assistants") or [])
                if assistant != "shared"
            }
        )
        doc_types = sorted({chunk.get("doc_type") for chunk in self.chunks if chunk.get("doc_type")})
        return {
            "model": EMBED_MODEL,
            "chunks": len(self.chunks),
            "documents": len(self.doc_chunks),
            "assistants": assistants,
            "doc_types": doc_types,
        }

    def documents(self):
        docs = []
        for doc_id, chunks in self.doc_chunks.items():
            first = chunks[0]
            docs.append(
                {
                    "doc_id": doc_id,
                    "doc_title": first.get("doc_title", ""),
                    "doc_no": first.get("doc_no", ""),
                    "doc_type": first.get("doc_type", ""),
                    "department": first.get("department", ""),
                    "assistants": first.get("assistants", []),
                    "source_file": first.get("source_file", ""),
                    "source_url": first.get("source_url", ""),
                    "publish_date": first.get("publish_date", ""),
                    "date_basis": first.get("date_basis", ""),
                    "pages": self.page_count(doc_id),
                    "chunks": len(chunks),
                    "file_url": self.file_url(doc_id),
                }
            )
        return sorted(docs, key=lambda item: (item["department"], item["doc_title"]))

    def document_chunks(self, doc_id):
        return list(self.doc_chunks.get(doc_id, []))
