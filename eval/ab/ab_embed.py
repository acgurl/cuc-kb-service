#!/usr/bin/env python3
"""Offline A/B of embedding models through the production retrieval pipeline.

Only query encoding and the dense stage are swapped (brute-force numpy instead of
Qdrant); BM25, RRF, bonuses, rerank and answerability come from search_core.KB.

    python eval/ab/ab_embed.py embed --model bge-m3
    python eval/ab/ab_embed.py eval --models bge-small-zh,bge-m3 --tag ab1

Embeddings are cached in data/ab/<model>.npy, aligned with data/chunks.jsonl.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kb import search_core  # noqa: E402
from kb.common import DATA_DIR, FASTEMBED_CACHE  # noqa: E402

AB_DIR = DATA_DIR / "ab"
OUT_DIR = Path(__file__).resolve().parent

BGE_ZH_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

# name -> backend config. "fastembed" reproduces production exactly.
MODELS = {
    "bge-small-zh": {"kind": "fastembed", "id": "BAAI/bge-small-zh-v1.5"},
    "bge-large-zh": {"kind": "st", "id": "BAAI/bge-large-zh-v1.5", "query_prefix": BGE_ZH_INSTRUCTION, "max_len": 512},
    "bge-m3": {"kind": "st", "id": "BAAI/bge-m3", "max_len": 1024},
    "qwen3-0.6b": {"kind": "st", "id": "Qwen/Qwen3-Embedding-0.6B", "prompt_name": "query", "max_len": 1024},
}

DATASETS = {
    "heldout": ROOT / "eval/heldout/dataset_heldout_v1.json",
    "v5": ROOT / "eval/dataset_v5_personas.json",
}
RERANK_MODES = ("off", "prod", "always")


def norm(text):
    return re.sub(r"\s+", "", text or "")


# ---- backends --------------------------------------------------------------


class FastEmbedBackend:
    def __init__(self, model_id):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name=model_id, cache_dir=str(FASTEMBED_CACHE))

    def encode_passages(self, texts):
        return np.vstack([np.asarray(v, dtype=np.float32) for v in self.model.embed(texts, batch_size=32)])

    def encode_query(self, query):
        try:
            vector = list(self.model.query_embed(query))[0]
        except AttributeError:
            vector = list(self.model.embed([BGE_ZH_INSTRUCTION + query]))[0]
        return np.asarray(vector, dtype=np.float32)


class SentenceTransformerBackend:
    def __init__(self, model_id, query_prefix="", prompt_name=None, max_len=512):
        import torch
        from sentence_transformers import SentenceTransformer

        device = "cuda" if torch.cuda.is_available() else "cpu"
        kwargs = {"torch_dtype": torch.float16} if device == "cuda" else {}
        self.model = SentenceTransformer(model_id, device=device, model_kwargs=kwargs)
        self.model.max_seq_length = max_len
        self.query_prefix = query_prefix
        self.prompt_name = prompt_name

    def encode_passages(self, texts):
        return self.model.encode(texts, batch_size=16, show_progress_bar=True, convert_to_numpy=True).astype(np.float32)

    def encode_query(self, query):
        if self.prompt_name:
            vector = self.model.encode([query], prompt_name=self.prompt_name, convert_to_numpy=True)[0]
        else:
            vector = self.model.encode([self.query_prefix + query], convert_to_numpy=True)[0]
        return vector.astype(np.float32)


def load_backend(name):
    config = MODELS[name]
    if config["kind"] == "fastembed":
        return FastEmbedBackend(config["id"])
    return SentenceTransformerBackend(
        config["id"],
        query_prefix=config.get("query_prefix", ""),
        prompt_name=config.get("prompt_name"),
        max_len=config.get("max_len", 512),
    )


def l2(matrix):
    return matrix / np.maximum(np.linalg.norm(matrix, axis=-1, keepdims=True), 1e-12)


def load_chunks():
    path = DATA_DIR / "chunks.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class CachedCrossEncoder:
    """Same model as production (@cf/baai/bge-reranker-base), on GPU, sigmoid scores.

    The Cloudflare worker returns sigmoid probabilities, so KB_RERANK_MIN_SCORE keeps
    its meaning. Scores are cached per (query, document) so every embedding model is
    reranked with identical numbers for identical candidates.
    """

    def __init__(self, model_id="BAAI/bge-reranker-base", max_length=512):
        import torch
        from sentence_transformers import CrossEncoder

        device = "cuda" if torch.cuda.is_available() else "cpu"
        kwargs = {"torch_dtype": torch.float16} if device == "cuda" and max_length > 512 else {}
        self.model = CrossEncoder(
            model_id, device=device, max_length=max_length,
            activation_fn=torch.nn.Sigmoid(), model_kwargs=kwargs,
        )
        self.cache = {}

    def score(self, query, documents):
        return self.model.predict([(query, d) for d in documents], batch_size=16, show_progress_bar=False)

    def rerank(self, query, documents):
        missing = [d for d in documents if (query, d) not in self.cache]
        if missing:
            for document, score in zip(missing, self.score(query, missing)):
                self.cache[(query, document)] = float(score)
        return [self.cache[(query, d)] for d in documents]


class QwenReranker(CachedCrossEncoder):
    """Qwen3-Reranker scored as P(yes) from the causal LM, per the model card."""

    INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"
    PREFIX = (
        "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query "
        'and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
        "<|im_start|>user\n"
    )
    SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

    def __init__(self, model_id, max_length=1024):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, padding_side="left")
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.model = AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=dtype).to(self.device).eval()
        self.yes = self.tokenizer.convert_tokens_to_ids("yes")
        self.no = self.tokenizer.convert_tokens_to_ids("no")
        self.prefix_ids = self.tokenizer.encode(self.PREFIX, add_special_tokens=False)
        self.suffix_ids = self.tokenizer.encode(self.SUFFIX, add_special_tokens=False)
        self.max_length = max_length
        self.cache = {}

    def score(self, query, documents, batch_size=8):
        torch = self.torch
        budget = self.max_length - len(self.prefix_ids) - len(self.suffix_ids)
        scores = []
        for start in range(0, len(documents), batch_size):
            pairs = [
                f"<Instruct>: {self.INSTRUCTION}\n<Query>: {query}\n<Document>: {d}"
                for d in documents[start : start + batch_size]
            ]
            encoded = self.tokenizer(
                pairs, padding=False, truncation="longest_first",
                return_attention_mask=False, max_length=budget,
            )
            encoded["input_ids"] = [self.prefix_ids + ids + self.suffix_ids for ids in encoded["input_ids"]]
            batch = self.tokenizer.pad(encoded, padding=True, return_tensors="pt").to(self.device)
            with torch.no_grad():
                # Left padding puts every pair's last token at -1; project only that
                # position instead of materializing vocab logits for the whole sequence.
                hidden = self.model.model(**batch).last_hidden_state[:, -1, :]
                logits = self.model.lm_head(hidden)
            pair = torch.stack([logits[:, self.no], logits[:, self.yes]], dim=1).float()
            scores.extend(torch.softmax(pair, dim=1)[:, 1].tolist())
        return scores


class BailianReranker(CachedCrossEncoder):
    """Bailian qwen3-rerank through the OpenAI-compatible endpoint used by CloudReranker.

    The key comes from DASHSCOPE_API_KEY or the bl CLI config. Scores are cached on
    disk so reruns do not bill the same (query, document) pair twice.
    """

    URL = "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"

    def __init__(self, model="qwen3-rerank"):
        import os

        key = os.environ.get("DASHSCOPE_API_KEY")
        if not key:
            config = Path.home() / ".bailian/config.json"
            key = json.loads(config.read_text(encoding="utf-8"))["api_key"]
        self.reranker = search_core.CloudReranker(
            base=self.URL, path="", model=model, api_key=key.strip(), header="authorization", timeout=30
        )
        self.reranker.url = self.URL
        self.cache_path = AB_DIR / f"rerank_cache_{model}.json"
        self.cache = {}
        if self.cache_path.exists():
            stored = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.cache = {(q, d): s for q, d, s in stored}

    def score(self, query, documents):
        for attempt in range(3):
            try:
                scores = self.reranker.rerank(query, documents)
                break
            except Exception as exc:  # transient network / rate limit
                if attempt == 2:
                    raise
                print(f"bailian rerank retry: {str(exc)[:120]}", flush=True)
                time.sleep(2 * (attempt + 1))
        self.cache.update({(query, d): float(s) for d, s in zip(documents, scores)})
        self.cache_path.write_text(
            json.dumps([[q, d, s] for (q, d), s in self.cache.items()], ensure_ascii=False), encoding="utf-8"
        )
        return scores


MS_CACHE = ROOT / ".cache/modelscope"
RERANKERS = {
    "bge-base": lambda: CachedCrossEncoder("BAAI/bge-reranker-base", 512),
    "bge-v2-m3": lambda: CachedCrossEncoder(str(MS_CACHE / "BAAI/bge-reranker-v2-m3"), 1024),
    "qwen3-rr-0.6b": lambda: QwenReranker(str(MS_CACHE / "Qwen/Qwen3-Reranker-0.6B"), 1024),
    "bl-qwen3-rerank": lambda: BailianReranker("qwen3-rerank"),
}


# ---- offline KB ------------------------------------------------------------


class OfflineKB(search_core.KB):
    """Production KB with numpy dense search over a cached embedding matrix."""

    def __init__(self, backend, matrix):
        super().__init__()
        self.backend = backend
        self.matrix = matrix
        self.rerank_mode = "prod"
        self.encode_seconds = []
        self.active_reranker = None
        self.rerank_seconds = []

    def load(self):
        import jieba
        from rank_bm25 import BM25Okapi

        self._load_chunks()
        self._load_doc_meta()
        if len(self.chunks) != len(self.matrix):
            raise SystemExit("embedding matrix is out of sync with chunks.jsonl; re-run embed")
        self.active = np.array([c.get("status") == "active" for c in self.chunks])
        self.bm25 = BM25Okapi([jieba.lcut_for_search(c["embed_text"]) for c in self.chunks])
        self.reranker = True  # truthy so search() takes the rerank branch

    def _rerank_scores(self, query, documents):
        started = time.perf_counter()
        scores = self.active_reranker.rerank(query, documents)
        self.rerank_seconds.append(time.perf_counter() - started)
        return scores

    def encode_query(self, query):
        started = time.perf_counter()
        vector = l2(self.backend.encode_query(query))
        self.encode_seconds.append(time.perf_counter() - started)
        return vector

    def dense_search(self, vector, query_filter, limit):
        # Only assistant=None / doc_types=None runs are supported: filter = active.
        scores = self.matrix @ vector
        scores = np.where(self.active, scores, -np.inf)
        top = np.argpartition(-scores, limit)[:limit]
        top = top[np.argsort(-scores[top])]
        return [(self.chunks[i]["chunk_id"], float(scores[i])) for i in top]

    def _should_rerank(self, query, best_dense):
        if self.rerank_mode == "off":
            return False
        if self.rerank_mode == "always":
            return True
        return super()._should_rerank(query, best_dense)


# ---- commands --------------------------------------------------------------


def cmd_embed(args):
    AB_DIR.mkdir(parents=True, exist_ok=True)
    chunks = load_chunks()
    backend = load_backend(args.model)
    started = time.time()
    matrix = l2(backend.encode_passages([c["embed_text"] for c in chunks]))
    np.save(AB_DIR / f"{args.model}.npy", matrix.astype(np.float32))
    (AB_DIR / f"{args.model}.ids.json").write_text(
        json.dumps([c["chunk_id"] for c in chunks]), encoding="utf-8"
    )
    print(f"{args.model}: {matrix.shape} in {time.time() - started:.1f}s")


def load_cases():
    sets = {}
    for name, path in DATASETS.items():
        data = json.loads(path.read_text(encoding="utf-8"))
        answerable = list(data.get("curated", [])) + list(data.get("short", []))
        sets[name] = {"answerable": answerable, "no_answer": list(data.get("no_answer", []))}
    return sets


def doc_rank(titles, expected):
    for rank, title in enumerate(titles, 1):
        if any(e in title for e in expected):
            return rank
    return None


def dense_recall(kb, case, vector):
    hits = kb.dense_search(vector, None, 40)
    titles = [kb.chunk_map[cid]["doc_title"] for cid, _ in hits]
    keys = [norm(k) for k in case["keywords"]]
    answer_rank = next(
        (
            rank
            for rank, (cid, _) in enumerate(hits, 1)
            if any(e in kb.chunk_map[cid]["doc_title"] for e in case["expected_docs"])
            and any(k in norm(kb.chunk_map[cid]["text"]) for k in keys)
        ),
        None,
    )
    return doc_rank(titles, case["expected_docs"]), answer_rank


def best_rerank(results):
    scores = [r["rerank_score"] for r in results if r["rerank_score"] is not None]
    return max(scores) if scores else None


def evaluate(kb, sets, mode):
    kb.rerank_mode = mode
    rows = []
    for set_name, groups in sets.items():
        for case in groups["answerable"]:
            response = kb.search(case["query"], top_k=5)
            results = response["results"]
            titles = [r["citation"]["doc_title"] for r in results]
            keys = [norm(k) for k in case["keywords"]]
            rows.append({
                "set": set_name, "id": case["id"], "query": case["query"], "type": "answerable",
                "rank": doc_rank(titles, case["expected_docs"]),
                "answer_hit": any(k in norm(r["text"]) for r in results for k in keys),
                "no_answer": response["no_answer"],
                "reranked": any(r["rerank_score"] is not None for r in results),
                "best_rerank": best_rerank(results),
                "top": titles[0] if titles else None,
                "took_ms": response["took_ms"],
            })
        for case in groups["no_answer"]:
            response = kb.search(case["query"], top_k=5)
            rows.append({
                "set": set_name, "id": case["id"], "query": case["query"], "type": "no_answer",
                "no_answer": response["no_answer"],
                "best_rerank": best_rerank(response["results"]),
                "top": response["results"][0]["citation"]["doc_title"] if response["results"] else None,
                "took_ms": response["took_ms"],
            })
    return rows


def summarize(rows, dense_rows):
    summary = {}
    for set_name in DATASETS:
        ans = [r for r in rows if r["set"] == set_name and r["type"] == "answerable"]
        na = [r for r in rows if r["set"] == set_name and r["type"] == "no_answer"]
        dense = [r for r in dense_rows if r["set"] == set_name]
        n = len(ans)
        summary[set_name] = {
            "n": n,
            "hit@1": sum(1 for r in ans if r["rank"] == 1),
            "hit@3": sum(1 for r in ans if r["rank"] and r["rank"] <= 3),
            "hit@5": sum(1 for r in ans if r["rank"] and r["rank"] <= 5),
            "mrr": round(sum(1 / r["rank"] for r in ans if r["rank"]) / max(n, 1), 4),
            "answer@5": sum(1 for r in ans if r["answer_hit"]),
            "answered": sum(1 for r in ans if not r["no_answer"]),
            "reranked": sum(1 for r in ans if r["reranked"]),
            "no_answer_ok": f"{sum(1 for r in na if r['no_answer'])}/{len(na)}",
            "dense_doc@10": sum(1 for r in dense if r["doc_rank"] and r["doc_rank"] <= 10),
            "dense_doc@40": sum(1 for r in dense if r["doc_rank"]),
            "dense_ans@10": sum(1 for r in dense if r["answer_rank"] and r["answer_rank"] <= 10),
            "dense_ans@40": sum(1 for r in dense if r["answer_rank"]),
        }
    return summary


def cmd_eval(args):
    sets = load_cases()
    chunks = load_chunks()
    rerankers = {name: RERANKERS[name]() for name in args.rerankers.split(",")}
    out = OUT_DIR / f"results_{args.tag}.json"
    report = {}
    for name in args.models.split(","):
        matrix = np.load(AB_DIR / f"{name}.npy")
        ids = json.loads((AB_DIR / f"{name}.ids.json").read_text(encoding="utf-8"))
        if ids != [c["chunk_id"] for c in chunks]:
            raise SystemExit(f"{name}: cached ids differ from chunks.jsonl; re-run embed")
        kb = OfflineKB(load_backend(name), matrix)
        kb.load()

        dense_rows = []
        for set_name, groups in sets.items():
            for case in groups["answerable"]:
                doc_r, ans_r = dense_recall(kb, case, kb.encode_query(case["query"]))
                dense_rows.append({"set": set_name, "id": case["id"], "doc_rank": doc_r, "answer_rank": ans_r})
        encode_ms = round(1000 * float(np.mean(kb.encode_seconds)), 1)

        for rr_name, reranker in rerankers.items():
            key = f"{name}+{rr_name}"
            kb.active_reranker = reranker
            report[key] = {"encode_ms_mean": encode_ms, "dense_rows": dense_rows}
            for mode in args.modes.split(","):
                rows = evaluate(kb, sets, mode)
                report[key][mode] = {"summary": summarize(rows, dense_rows), "rows": rows}
                print(key, mode, json.dumps(report[key][mode]["summary"], ensure_ascii=False), flush=True)
                out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        del kb
    print("wrote", out)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    embed = sub.add_parser("embed")
    embed.add_argument("--model", required=True, choices=sorted(MODELS))
    evaluate_parser = sub.add_parser("eval")
    evaluate_parser.add_argument("--models", required=True)
    evaluate_parser.add_argument("--modes", default=",".join(RERANK_MODES))
    evaluate_parser.add_argument("--rerankers", default="bge-base")
    evaluate_parser.add_argument("--tag", default="ab")
    args = parser.parse_args()
    {"embed": cmd_embed, "eval": cmd_eval}[args.cmd](args)


if __name__ == "__main__":
    main()
