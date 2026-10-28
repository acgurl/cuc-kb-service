import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import jieba
import numpy as np
from fastembed import TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue
from rank_bm25 import BM25Okapi

from common import (
    COLLECTION,
    DATA_DIR,
    EMBED_MODEL,
    FASTEMBED_CACHE,
    FILE_OBJECT_PREFIX,
    PUBLIC_BASE_URL,
    QDRANT_API_KEY,
    QDRANT_URL,
    QUERY_INSTRUCTION,
    RERANK_API_BASE,
    RERANK_API_HEADER,
    RERANK_API_KEY,
    RERANK_API_MODEL,
    RERANK_API_PATH,
    RERANK_API_TIMEOUT,
    RERANK_API_USER_AGENT,
    RERANK_LOCAL_FALLBACK,
    RERANK_LONG_MIN_CHARS,
    RERANK_LONG_TRIGGER,
    RERANK_MIN_SCORE,
    RERANK_MODEL,
    RERANK_PROVIDER,
    RERANK_SHORTLIST,
    RERANK_TRIGGER,
)

DOC_NO_RE = re.compile(r"[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*\d+\s*号")

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
GENERIC_RERANK_PENALTY = float(os.getenv("KB_GENERIC_RERANK_PENALTY", "0.10"))
GENERIC_RRF_FACTOR = float(os.getenv("KB_GENERIC_RRF_FACTOR", "0.85"))
GENERIC_CLOSE_MARGIN = float(os.getenv("KB_GENERIC_CLOSE_MARGIN", "0.10"))

CANDIDATE_POOL = int(os.getenv("KB_CANDIDATE_POOL", "40"))
CANDIDATE_LIMIT = int(os.getenv("KB_CANDIDATE_LIMIT", "24"))
MAX_CHUNKS_PER_DOC = int(os.getenv("KB_MAX_CHUNKS_PER_DOC", "16"))
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
COVERAGE_BONUS = float(os.getenv("KB_COVERAGE_BONUS", "0.004"))
COVERAGE_MAX_BONUS = float(os.getenv("KB_COVERAGE_MAX_BONUS", "0.03"))
INTENT_PHRASES = (
    "基本条件", "申请条件", "评选条件", "岗位聘用", "聘用条件",
    "学分认定", "报销比例", "自付比例", "收费标准", "办理流程",
    "申请材料", "负责部门", "责任人", "学位申请",
)
PHRASE_BONUS = float(os.getenv("KB_PHRASE_BONUS", "0.03"))
TITLE_PHRASE_BONUS = float(os.getenv("KB_TITLE_PHRASE_BONUS", "0.06"))
DEFINITION_HINTS = ("依据", "定义", "是什么", "指什么", "含义")
DURATION_HINTS = ("多久", "多长时间", "期限", "最长", "可以延期")

ANSWERABILITY_MAX_CONF = float(os.getenv("KB_ANSWERABILITY_MAX_CONF", "0.95"))
CONTENT_ANSWERABILITY_MAX_CONF = float(
    os.getenv("KB_CONTENT_ANSWERABILITY_MAX_CONF", "0.97")
)
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
ANSWER_SPAN_WINDOW = int(os.getenv("KB_ANSWER_SPAN_WINDOW", "50"))
TEMPORAL_HINTS = ("今天", "今日", "本周", "本月", "今年")


def detect_attribute(query):
    for attribute, hints in ATTRIBUTE_HINTS.items():
        if any(hint in query for hint in hints):
            return attribute
    return None


def query_entities(query):
    return {
        term
        for term in jieba.lcut_for_search(query)
        if len(term) >= 2 and term not in ENTITY_STOPWORDS
    }


def has_answer_span(text, attribute, entities, query=""):
    if not entities:
        return False
    temporal = next((item for item in TEMPORAL_HINTS if item in query), None)
    ordinals = re.findall(r"第\d+[次条章节]|\d+次", query)
    for match in SPAN_PATTERNS[attribute].finditer(text):
        window = text[
            max(0, match.start() - ANSWER_SPAN_WINDOW) :
            match.end() + ANSWER_SPAN_WINDOW
        ]
        if not any(entity in window for entity in entities):
            continue
        if ordinals and not any(item in window for item in ordinals):
            continue
        if temporal and not (
            temporal in window
            or re.search(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}|\d{1,2}月\d{1,2}日", window)
        ):
            continue
        return True
    return False


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def is_generic_chunk(chunk) -> bool:
    if not chunk:
        return False
    if chunk.get("is_generic"):
        return True
    title = chunk.get("doc_title", "")
    return any(title.startswith(prefix) for prefix in GENERIC_TITLE_PREFIXES)


class CloudReranker:
    def __init__(self, base, path, model, api_key, header, timeout):
        self.url = base.rstrip("/") + "/" + path.lstrip("/")
        self.model = model
        self.api_key = api_key
        self.header = header
        self.timeout = timeout

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
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

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


class KB:
    def __init__(self):
        self.chunks = []
        self.chunk_map = {}
        self.doc_meta = {}
        self.page_counts = {}
        self.bm25 = None
        self.model = None
        self.reranker = None
        self.cloud_reranker = None
        self.client = None
        self.lock = threading.Lock()
        self.loaded_at = None

    def load(self):
        started = time.time()
        chunks_path = DATA_DIR / "chunks.jsonl"
        if not chunks_path.exists():
            raise RuntimeError(f"missing {chunks_path}, run ingest first")
        with chunks_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    chunk = json.loads(line)
                    self.chunks.append(chunk)
                    self.chunk_map[chunk["chunk_id"]] = chunk

        docs_path = DATA_DIR / "docs.json"
        if docs_path.exists():
            try:
                for doc in json.loads(docs_path.read_text(encoding="utf-8")):
                    if doc.get("doc_id"):
                        self.doc_meta[doc["doc_id"]] = doc
            except (OSError, json.JSONDecodeError) as exc:
                print(f"cannot load {docs_path}: {exc}", flush=True)
        self.page_counts = {
            doc_id: int(doc.get("pages") or 0)
            for doc_id, doc in self.doc_meta.items()
        }
        for chunk in self.chunks:
            doc_id = chunk["doc_id"]
            self.page_counts[doc_id] = max(
                self.page_counts.get(doc_id, 0),
                int(chunk.get("page_end") or 0),
            )

        tokenized = [jieba.lcut_for_search(chunk["embed_text"]) for chunk in self.chunks]
        self.bm25 = BM25Okapi(tokenized) if tokenized else None

        self.model = TextEmbedding(
            model_name=EMBED_MODEL,
            cache_dir=str(FASTEMBED_CACHE),
        )
        if RERANK_PROVIDER in ("openai", "cloud", "openai-compatible"):
            if not RERANK_API_BASE or not RERANK_API_MODEL:
                raise RuntimeError(
                    "KB_RERANK_PROVIDER=openai requires RERANK_API_BASE and RERANK_API_MODEL"
                )
            self.cloud_reranker = CloudReranker(
                base=RERANK_API_BASE,
                path=RERANK_API_PATH,
                model=RERANK_API_MODEL,
                api_key=RERANK_API_KEY,
                header=RERANK_API_HEADER,
                timeout=RERANK_API_TIMEOUT,
            )
            print(
                f"cloud reranker: {self.cloud_reranker.url} model={RERANK_API_MODEL}",
                flush=True,
            )
            if RERANK_LOCAL_FALLBACK and RERANK_MODEL:
                try:
                    self.reranker = TextCrossEncoder(
                        model_name=RERANK_MODEL,
                        cache_dir=str(FASTEMBED_CACHE),
                    )
                except Exception as exc:
                    print(f"local rerank fallback unavailable: {exc}", flush=True)
        elif RERANK_PROVIDER == "fastembed" and RERANK_MODEL:
            try:
                self.reranker = TextCrossEncoder(
                    model_name=RERANK_MODEL,
                    cache_dir=str(FASTEMBED_CACHE),
                )
            except Exception as exc:
                print(f"reranker unavailable: {exc}", flush=True)
        self.client = QdrantClient(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY or None,
            timeout=60,
        )
        self.loaded_at = time.time()
        print(
            f"KB loaded: {len(self.chunks)} chunks in {self.loaded_at - started:.1f}s",
            flush=True,
        )

    def encode_query(self, query: str):
        with self.lock:
            try:
                vector = list(self.model.query_embed(query))[0]
            except AttributeError:
                vector = list(self.model.embed([QUERY_INSTRUCTION + query]))[0]
        vector = np.asarray(vector, dtype=np.float32)
        return vector / max(float(np.linalg.norm(vector)), 1e-12)

    def _filter(self, assistant, doc_types):
        conditions = [FieldCondition(key="status", match=MatchValue(value="active"))]
        if assistant:
            conditions.append(
                FieldCondition(
                    key="assistants",
                    match=MatchAny(any=[assistant, "shared"]),
                )
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

    def file_url(self, doc_id, page=None):
        if not doc_id or doc_id == "qa-csv":
            return ""
        url = f"{PUBLIC_BASE_URL}/v1/files/{quote(doc_id, safe='')}"
        if page:
            url += f"#page={int(page)}"
        return url

    def page_count(self, doc_id):
        return int(self.page_counts.get(doc_id, 0))

    def bm25_search(self, query, limit, assistant, doc_types):
        if not self.bm25:
            return []
        tokens = jieba.lcut_for_search(query)
        scores = self.bm25.get_scores(tokens)
        candidates = []
        for index, score in enumerate(scores):
            if score <= 0:
                continue
            chunk = self.chunks[index]
            if self._matches(chunk, assistant, doc_types):
                candidates.append((chunk["chunk_id"], float(score)))
        candidates.sort(key=lambda item: item[1], reverse=True)
        return candidates[:limit]

    def search(self, query, assistant=None, doc_types=None, top_k=5):
        started = time.time()
        query = (query or "").strip()
        if not query:
            return {"query": query, "results": [], "no_answer": True, "took_ms": 0}

        query_filter = self._filter(assistant, doc_types)
        vector = self.encode_query(query)

        try:
            response = self.client.query_points(
                collection_name=COLLECTION,
                query=vector.tolist(),
                using="dense",
                query_filter=query_filter,
                limit=40,
                with_payload=True,
            )
            dense_points = response.points
        except Exception as exc:
            print(f"qdrant search failed: {exc}", flush=True)
            dense_points = []

        dense_scores = {}
        fused = {}
        for rank, point in enumerate(dense_points):
            chunk_id = (point.payload or {}).get("chunk_id")
            if not chunk_id:
                continue
            dense_scores[chunk_id] = float(point.score)
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (60 + rank + 1)

        bm25_scores = {}
        for rank, (chunk_id, score) in enumerate(
            self.bm25_search(query, 40, assistant, doc_types)
        ):
            bm25_scores[chunk_id] = score
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (60 + rank + 1)

        doc_no_match = DOC_NO_RE.search(query)
        target_doc_no = normalize(doc_no_match.group(0)) if doc_no_match else ""
        query_terms = {
            term
            for term in jieba.lcut_for_search(query)
            if len(term) >= 2 and term not in COVERAGE_STOPWORDS
        }
        for chunk_id in list(fused):
            chunk = self.chunk_map.get(chunk_id)
            if not chunk:
                continue
            if target_doc_no and target_doc_no in normalize(chunk.get("doc_no", "")):
                fused[chunk_id] += 0.08
            if chunk.get("is_qa"):
                fused[chunk_id] += 0.004
            if query_terms and not is_generic_chunk(chunk):
                text = chunk.get("text", "")
                coverage = sum(1 for term in query_terms if term in text)
                if coverage:
                    fused[chunk_id] += min(
                        COVERAGE_BONUS * coverage, COVERAGE_MAX_BONUS
                    )

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
        best_dense_all = max(dense_scores.values(), default=0.0)
        rerank_scores = {}
        intent_rerank = any(hint in query for hint in INTENT_RERANK_HINTS)
        should_rerank = intent_rerank or best_dense_all < RERANK_TRIGGER or (
            len(query) >= RERANK_LONG_MIN_CHARS and best_dense_all < RERANK_LONG_TRIGGER
        )
        if (self.cloud_reranker or self.reranker) and candidates and should_rerank:
            shortlist = candidates[:max(RERANK_SHORTLIST, CANDIDATE_LIMIT)]
            documents = [
                self.chunk_map[chunk_id]["embed_text"]
                for chunk_id, _ in shortlist
                if chunk_id in self.chunk_map
            ]
            scores = None
            if self.cloud_reranker:
                try:
                    scores = self.cloud_reranker.rerank(query, documents)
                except Exception as exc:
                    print(f"cloud rerank failed: {exc}", flush=True)
                    if self.reranker:
                        try:
                            scores = list(self.reranker.rerank(query, documents))
                        except Exception as fallback_exc:
                            print(
                                f"local rerank fallback failed: {fallback_exc}",
                                flush=True,
                            )
            elif self.reranker:
                try:
                    scores = list(self.reranker.rerank(query, documents))
                except Exception as exc:
                    print(f"rerank failed: {exc}", flush=True)

            if scores:
                for (chunk_id, _), score in zip(shortlist, scores):
                    if score is not None:
                        rerank_scores[chunk_id] = float(score)
                reranked = [
                    item for item in shortlist if item[0] in rerank_scores
                ]

                generic_best = max(
                    (
                        rerank_scores[item[0]]
                        for item in shortlist
                        if item[0] in rerank_scores
                        and is_generic_chunk(self.chunk_map.get(item[0]))
                    ),
                    default=None,
                )
                specific_best = max(
                    (
                        rerank_scores[item[0]]
                        for item in shortlist
                        if item[0] in rerank_scores
                        and not is_generic_chunk(self.chunk_map.get(item[0]))
                    ),
                    default=None,
                )
                apply_generic_penalty = (
                    generic_best is not None
                    and specific_best is not None
                    and specific_best >= generic_best - GENERIC_CLOSE_MARGIN
                )

                def generic_adjusted_rerank(item):
                    score = rerank_scores[item[0]]
                    chunk = self.chunk_map.get(item[0]) or {}
                    if apply_generic_penalty and is_generic_chunk(chunk):
                        score -= GENERIC_RERANK_PENALTY
                    elif not is_generic_chunk(chunk):
                        title = chunk.get("doc_title", "")
                        text = chunk.get("text", "")
                        if any(
                            phrase in query and phrase in title
                            for phrase in INTENT_PHRASES
                        ):
                            score += TITLE_PHRASE_BONUS
                        elif any(
                            phrase in query and phrase in text
                            for phrase in INTENT_PHRASES
                        ):
                            score += PHRASE_BONUS
                        if any(hint in query for hint in DEFINITION_HINTS) and (
                            "是指" in text or "指的是" in text
                        ):
                            score += PHRASE_BONUS
                        if any(hint in query for hint in DURATION_HINTS) and (
                            "最长不超过" in text
                            or (
                                "不超过" in text
                                and SPAN_PATTERNS["time"].search(text)
                            )
                        ):
                            score += PHRASE_BONUS
                    return score

                reranked.sort(key=generic_adjusted_rerank, reverse=True)
                others = [
                    item for item in candidates if item[0] not in rerank_scores
                ]
                candidates = reranked + others

        if not rerank_scores and candidates:
            generic_best = max(
                (
                    score
                    for chunk_id, score in candidates
                    if is_generic_chunk(self.chunk_map.get(chunk_id))
                ),
                default=None,
            )
            specific_best = max(
                (
                    score
                    for chunk_id, score in candidates
                    if not is_generic_chunk(self.chunk_map.get(chunk_id))
                ),
                default=None,
            )
            if (
                generic_best is not None
                and specific_best is not None
                and specific_best >= generic_best * GENERIC_RRF_FACTOR
            ):
                candidates.sort(
                    key=lambda item: item[1]
                    * (
                        GENERIC_RRF_FACTOR
                        if is_generic_chunk(self.chunk_map.get(item[0]))
                        else 1.0
                    ),
                    reverse=True,
                )

        results = []
        for chunk_id, score in candidates[:top_k]:
            chunk = self.chunk_map.get(chunk_id)
            if not chunk:
                continue
            results.append(
                {
                    "chunk_id": chunk_id,
                    "doc_id": chunk["doc_id"],
                    "text": chunk["text"],
                    "score": round(score, 6),
                    "dense_score": round(dense_scores.get(chunk_id, 0.0), 4),
                    "bm25_score": round(bm25_scores.get(chunk_id, 0.0), 4),
                    "rerank_score": (
                        round(rerank_scores[chunk_id], 4)
                        if chunk_id in rerank_scores
                        else None
                    ),
                    "citation": {
                        "doc_title": chunk.get("doc_title", ""),
                        "doc_no": chunk.get("doc_no", ""),
                        "doc_type": chunk.get("doc_type", ""),
                        "clause_no": chunk.get("clause_no", ""),
                        "page_start": chunk.get("page_start", 0),
                        "page_end": chunk.get("page_end", 0),
                        "section_path": chunk.get("section_path", []),
                        "source_file": chunk.get("source_file", ""),
                        "page_count": self.page_count(chunk["doc_id"]),
                        "file_url": self.file_url(
                            chunk["doc_id"], chunk.get("page_start") or None
                        ),
                    },
                    "is_qa": chunk.get("is_qa", False),
                    "is_generic": is_generic_chunk(chunk),
                }
            )

        best_dense = max([item["dense_score"] for item in results], default=0.0)
        best_bm25 = max([item["bm25_score"] for item in results], default=0.0)
        best_rerank = max(
            [item["rerank_score"] for item in results if item["rerank_score"] is not None],
            default=None,
        )
        if best_rerank is not None:
            no_answer = best_rerank < RERANK_MIN_SCORE
            confidence = best_rerank
        else:
            no_answer = not results or (best_dense < 0.45 and best_bm25 <= 0.0)
            confidence = best_dense

        attribute = detect_attribute(query) if results else None
        answerability = None
        if attribute:
            limit = (
                CONTENT_ANSWERABILITY_MAX_CONF
                if attribute == "content"
                else ANSWERABILITY_MAX_CONF
            )
            if confidence < limit:
                entities = query_entities(query)
                answerable = any(
                    has_answer_span(item["text"], attribute, entities, query)
                    for item in results[:5]
                )
                answerability = {"attribute": attribute, "answerable": answerable}
                if answerable:
                    no_answer = False
                elif attribute in ("price", "time", "count", "content", "person"):
                    no_answer = True

        return {
            "query": query,
            "results": results,
            "no_answer": no_answer,
            "confidence": round(float(confidence), 4),
            "answerability": answerability,
            "took_ms": int((time.time() - started) * 1000),
        }

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
        documents = {chunk["doc_id"] for chunk in self.chunks}
        return {
            "model": EMBED_MODEL,
            "chunks": len(self.chunks),
            "documents": len(documents),
            "assistants": assistants,
            "doc_types": doc_types,
        }

    def documents(self):
        docs = {}
        for chunk in self.chunks:
            doc_id = chunk["doc_id"]
            entry = docs.setdefault(
                doc_id,
                {
                    "doc_id": doc_id,
                    "doc_title": chunk.get("doc_title", ""),
                    "doc_no": chunk.get("doc_no", ""),
                    "doc_type": chunk.get("doc_type", ""),
                    "department": chunk.get("department", ""),
                    "assistants": chunk.get("assistants", []),
                    "source_file": chunk.get("source_file", ""),
                    "pages": 0,
                    "chunks": 0,
                },
            )
            entry["chunks"] += 1
            entry["pages"] = max(entry["pages"], chunk.get("page_end", 0))
        for entry in docs.values():
            entry["pages"] = self.page_count(entry["doc_id"])
            entry["file_url"] = self.file_url(entry["doc_id"])
        return sorted(docs.values(), key=lambda item: (item["department"], item["doc_title"]))

    def document_chunks(self, doc_id):
        return [
            chunk
            for chunk in self.chunks
            if chunk["doc_id"] == doc_id
        ]
