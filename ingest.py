#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import os
import re
import time
import uuid
from collections import Counter
from pathlib import Path

import pymupdf

from common import (
    COLLECTION,
    EMBED_MODEL,
    FASTEMBED_CACHE,
    QDRANT_API_KEY,
    QDRANT_URL,
)

PAGE_ANCHOR_RE = re.compile(r"^<!-- page: (\d+) -->$")
CLAUSE_RE = re.compile(r"^\s*(第[一二三四五六七八九十百千零〇\d]+条)\s*[、.．:：]?\s*")
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百千零〇\d]+[章编]")
SECTION_RE = re.compile(r"^第[一二三四五六七八九十百千零〇\d]+节")
LIST_RE = re.compile(
    r"^\s*(\d{1,2}[.、)）]|[（(]\d{1,2}[）)]|[①-⑳]|[一二三四五六七八九十]{1,3}[、.．])"
)
PAGE_NUM_RE = re.compile(
    r"^\s*[-—–]?\s*\d{1,4}\s*[-—–]?\s*$|^\s*第\s*\d+\s*页\s*(共\s*\d+\s*页)?\s*$"
)
DOC_NO_RE = re.compile(
    r"[\u4e00-\u9fa5A-Za-z]{0,30}[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*\d+\s*号"
)

SUPERSEDED_TITLES = {
    "2023年 中国传媒大学学生工作规章制度汇编",
}

SUPERSEDED_DOC_IDS = {
    # 2026-10-28 新增版本已取代现有语料中的旧版，软标记为 superseded，保留引用链接
    "d499cb99b6cce",  # 2021 本科生学籍 -> 2025-195
    "dd49debfa84e9",  # 2012 招标采购 -> 2024-231
    "d10f3c4ed61f6",  # 2005 公费医疗 -> 2019-124
    "d1af66fed07e7",  # 2005 公费医疗 docx -> 2019-124
    "dc2dfa8719d3e",  # 2023 博士简章 -> 2025 博士简章
}

DEPARTMENT_MAP = {
    "中传小勤": "后勤保障处",
    "中传小资": "国有资产管理处",
    "中传教务君": "本科生院",
    "团委小助手": "团委",
    "党建知识助手": "党委组织部",
    "中传培训小帮手": "培训学院",
    "财务处小助手": "财务处",
    "中传教服": "教师服务",
    "新生帮手": "学生工作部",
    "问道中传": "信息化处",
    "督导处小助手": "督导处",
    "信通迎新助手": "信息与通信工程学院",
    "档小管": "档案馆",
}

ASSISTANT_DOC_PREFIXES = {
    "中传财字": ["财务处小助手"],
    "中传教字": ["中传教务君"],
    "本科生院": ["中传教务君"],
    "中传资产字": ["中传小资"],
    "中传实验字": ["中传小资"],
    "中传资产后勤字": ["中传小勤"],
    "中传后勤字": ["中传小勤"],
    "中传党组字": ["党建知识助手"],
    "组字": ["党建知识助手"],
    "中传学字": ["新生帮手"],
    "中传学工字": ["新生帮手"],
}

ASSISTANT_TITLE_HINTS = (
    ("学生公寓管理办法", ["中传小勤"]),
    ("修缮工程", ["中传小资"]),
    ("内部控制", ["财务处小助手"]),
    ("科研项目经费", ["财务处小助手"]),
    ("修学指南", ["中传教务君"]),
    ("本科生院服务常见问题", ["中传教务君"]),
    ("选课管理办法", ["中传教务君"]),
    ("在读证明", ["中传教务君"]),
    ("重考报名", ["中传教务君"]),
    ("食堂", ["中传小勤"]),
    ("公务用车", ["中传小勤"]),
    ("退宿", ["中传小勤"]),
    ("节约能源", ["中传小勤"]),
    ("采购管理办法", ["中传小资"]),
    ("实验室", ["中传小资"]),
    ("收费管理办法", ["财务处小助手"]),
    ("差旅费", ["财务处小助手"]),
    ("科研经费报销", ["财务处小助手"]),
    ("后勤服务指南", ["新生帮手", "中传小勤"]),
    ("学生公寓服务中心", ["问道中传", "新生帮手"]),
    ("校园管理中心", ["中传小勤"]),
    ("饮食服务中心", ["中传小勤"]),
    ("共青团", ["团委小助手"]),
    ("团学系统", ["团委小助手"]),
    ("督导处", ["督导处小助手"]),
)

GENERIC_TITLE_PREFIXES = (
    "中国传媒大学学生工作规章制度汇编",
    "参考：教师手册",
    "修学指南2025版",
    "QA知识导入模板",
    "2026年迎接新生开学相关工作",
    "学校部门分类",
)

# Document-side aliases: keep query rewriting out of the retrieval layer.
DOC_SEARCH_ALIASES = (
    ("校园卡管理办法", "校园一卡通（一卡通）即校园卡。"),
)


def mark_generic(records):
    for record in records:
        record["is_generic"] = any(
            record["title"].startswith(prefix) for prefix in GENERIC_TITLE_PREFIXES
        )


def apply_assistant_overrides(records):
    for record in records:
        extra = []
        for prefix, assistants in ASSISTANT_DOC_PREFIXES.items():
            if record["doc_no"].startswith(prefix):
                extra.extend(assistants)
        for keyword, assistants in ASSISTANT_TITLE_HINTS:
            if keyword in record["title"]:
                extra.extend(assistants)
        for assistant in extra:
            if assistant not in record["assistants"]:
                record["assistants"].append(assistant)
        if extra:
            record["department"] = DEPARTMENT_MAP.get(extra[0], record["department"])


def clean_cell(value) -> str:
    text = "" if value is None else str(value)
    return text.replace("\n", " ").replace("|", "\\|").strip()


def rows_to_md(rows) -> str:
    rows = [[clean_cell(c) for c in row] for row in rows if row]
    rows = [r for r in rows if any(r)]
    if len(rows) < 2:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    for row in rows[1:]:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def extract_page(page):
    table_rects = []
    table_blocks = []
    try:
        finder = page.find_tables()
        for table in finder.tables:
            try:
                md = rows_to_md(table.extract())
                if md:
                    table_blocks.append(md)
                    table_rects.append(pymupdf.Rect(table.bbox))
            except Exception:
                continue
    except Exception:
        pass

    raw_lines = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        rect = pymupdf.Rect(block["bbox"])
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans).strip()
            if not text:
                continue
            size = max((s.get("size", 0) for s in spans), default=0)
            raw_lines.append((text, size, rect))

    if table_rects:
        filtered = [
            item for item in raw_lines
            if not any(item[2].intersects(tr) for tr in table_rects)
        ]
        if len("".join(x[0] for x in filtered)) >= 0.25 * len("".join(x[0] for x in raw_lines)):
            raw_lines = filtered

    return [(text, size) for text, size, _ in raw_lines], table_blocks


def drop_repeated_lines(pages) -> set:
    total = len(pages)
    counts = Counter()
    for lines, _ in pages:
        seen = {line[0].strip() for line in lines if len(line[0].strip()) <= 60}
        counts.update(seen)
    threshold = max(3, int(total * 0.4))
    return {text for text, count in counts.items() if count >= threshold}


def join_lines(lines) -> str:
    out = ""
    for line in lines:
        if (
            out
            and out[-1].isascii()
            and out[-1].isalnum()
            and line
            and line[0].isascii()
            and line[0].isalnum()
        ):
            out += " " + line
        else:
            out += line
    return out


def is_heading(text: str, size: float, body_size: float) -> bool:
    if len(text) > 80:
        return False
    if CHAPTER_RE.match(text) or SECTION_RE.match(text):
        return True
    if size >= body_size * 1.15 and len(text) <= 60:
        return True
    if re.match(r"^[一二三四五六七八九十]+、", text) and len(text) <= 40:
        return True
    return False


def render_md(pages) -> str:
    drop = drop_repeated_lines(pages)
    weighted = Counter()
    for lines, _ in pages:
        for text, size in lines:
            if text in drop or len(text) > 80:
                continue
            weighted[round(size, 1)] += len(text)
    body_size = weighted.most_common(1)[0][0] if weighted else 10.0

    parts = []
    for page_no, (lines, tables) in enumerate(pages, 1):
        parts.append(f"<!-- page: {page_no} -->")
        paragraph = []

        def flush():
            if paragraph:
                text = join_lines(paragraph).strip()
                if text:
                    parts.append(text)
                paragraph.clear()

        for text, size in lines:
            text = text.strip()
            if not text or text in drop or PAGE_NUM_RE.match(text):
                continue
            if re.search(r"[.．·]{8,}", text):
                continue
            if is_heading(text, size, body_size):
                flush()
                parts.append("## " + text)
            elif CLAUSE_RE.match(text) or (LIST_RE.match(text) and len(text) < 200):
                flush()
                parts.append(text)
            else:
                paragraph.append(text)
        flush()
        for index, table in enumerate(tables, 1):
            parts.append(f"**表格 {page_no}-{index}（第{page_no}页）**")
            parts.append(table)
    return "\n\n".join(parts)


def extract_pdf(path: Path):
    doc = pymupdf.open(path)
    pages = [extract_page(page) for page in doc]
    md = render_md(pages)
    plain = "".join(text for lines, _ in pages for text, _ in lines)
    return md, plain, len(doc)


def clean_title(stem: str) -> str:
    stem = re.sub(r"^\d{1,3}[-_.、]\s*", "", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem


def find_doc_no(title: str) -> str:
    match = DOC_NO_RE.search(title)
    if match:
        return re.sub(r"\s+", "", match.group(0))
    match = re.search(r"第\d+号", title)
    return match.group(0) if match else ""


def core_title(title: str) -> str:
    text = re.sub(r"^[\d\-_.、]+", "", title)
    text = re.sub(
        r"^[\u4e00-\u9fa5A-Za-z]{2,10}?[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*\d+\s*号",
        "",
        text,
    )
    text = re.sub(r"^第\d+号", "", text)
    text = re.sub(r"关于印发|的通知|通知|_view|\.(pdf|docx?)$", "", text, flags=re.I)
    text = re.sub(r"(docx?|pdf|view)$", "", text, flags=re.I)
    text = re.sub(r"20\d{2}[-_.年]\d{1,2}[-_.月]\d{1,2}日?", "", text)
    text = re.sub(r"[《》\s_（）()〔〕\[\]【】、，,。.．:：\-—]", "", text)
    return text


def classify(title: str, md: str) -> str:
    if any(k in title for k in ("常见问题", "问答", "快问快答", "FAQ", "知识库")):
        return "问答"
    if any(k in title for k in ("办法", "规定", "条例", "规程", "细则", "制度", "意见", "通知")):
        return "制度"
    if any(k in title for k in ("手册", "指南", "操作", "说明", "指导", "服务链接", "服务_")):
        return "指南"
    sample = title + md[:2000]
    if any(k in sample for k in ("常见问题", "问答", "快问快答", "FAQ")):
        return "问答"
    if any(k in sample for k in ("办法", "规定", "条例", "规程", "细则", "制度", "意见", "通知")):
        return "制度"
    return "资料"


def looks_like_toc(text: str) -> bool:
    doc_nos = len(DOC_NO_RE.findall(text))
    sentences = text.count("。") + text.count("；")
    if doc_nos >= 2 and sentences <= 1:
        return True
    if text.count("…") >= 2 and sentences <= 1:
        return True
    return False


def split_long(text: str, limit: int = 450):
    if len(text) <= limit:
        return [text]
    sentences = re.split(r"(?<=[。！？；])", text)
    parts = []
    current = ""
    for sentence in sentences:
        while len(sentence) > limit:
            cut = sentence.rfind("，", 0, limit)
            if cut < limit * 0.6:
                cut = limit
            if current:
                parts.append(current)
                current = ""
            parts.append(sentence[: cut + 1])
            sentence = sentence[cut + 1 :]
        if len(current) + len(sentence) <= limit:
            current += sentence
        else:
            if current:
                parts.append(current)
            current = sentence
    if current:
        parts.append(current)
    return [p for p in parts if p.strip()]


def split_table(md: str, limit: int = 1200):
    if len(md) <= limit:
        return [md]
    lines = md.splitlines()
    if len(lines) < 3:
        return [md]
    header, rows = lines[:2], lines[2:]
    parts = []
    current = "\n".join(header)
    for row in rows:
        if len(current) + len(row) + 1 > limit:
            parts.append(current)
            current = "\n".join(header)
        current += "\n" + row
    parts.append(current)
    return parts


def chunk_document(record):
    lines = record["md"].splitlines()
    clause_count = sum(1 for line in lines if CLAUSE_RE.match(line))
    clause_mode = clause_count >= 3
    chunks = []
    section = []
    page = 1
    buffer = []
    start_page = None
    end_page = None
    buffer_clause = None
    buffer_section = []

    def emit(text, page_start, page_end, clause=None, section_path=None, is_table=False):
        text = text.strip()
        if len(text) < 20 or looks_like_toc(text):
            return
        section_path = section_path or []
        prefix = f"《{record['title']}》"
        if section_path:
            prefix += " " + " > ".join(section_path)
        if clause:
            prefix += f" {clause}"
        alias = next(
            (
                alias_text
                for title_key, alias_text in DOC_SEARCH_ALIASES
                if title_key in record["title"]
            ),
            "",
        )
        chunks.append(
            {
                "chunk_id": f"{record['doc_id']}-{len(chunks):04d}",
                "doc_id": record["doc_id"],
                "doc_title": record["title"],
                "doc_no": record["doc_no"],
                "doc_type": record["doc_type"],
                "department": record["department"],
                "assistants": record["assistants"],
                "section_path": section_path,
                "clause_no": clause or "",
                "page_start": page_start,
                "page_end": page_end,
                "source_file": record["source_file"],
                "text": text,
                "embed_text": (alias + "\n" if alias else "") + prefix + "\n" + text,
                "is_qa": False,
                "is_generic": record.get("is_generic", False),
                "is_table": is_table,
                "status": (
                    "superseded"
                    if record["doc_id"] in SUPERSEDED_DOC_IDS
                    or record["title"] in SUPERSEDED_TITLES
                    else "active"
                ),
            }
        )

    def flush():
        nonlocal buffer, start_page, end_page, buffer_clause, buffer_section
        if not buffer:
            return
        text = join_lines(buffer).strip()
        if text:
            emit(text, start_page or page, end_page or page, buffer_clause, buffer_section)
        buffer = []
        start_page = None
        end_page = None
        buffer_clause = None

    def append(text):
        nonlocal buffer, start_page, end_page, buffer_section
        if not buffer:
            start_page = page
            end_page = page
            buffer_section = list(section)
        else:
            end_page = page
        buffer.append(text)

    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue

        anchor = PAGE_ANCHOR_RE.match(line)
        if anchor:
            page = int(anchor.group(1))
            index += 1
            continue

        if line.startswith("## "):
            flush()
            title = line[3:].strip()
            if CHAPTER_RE.match(title):
                section = [title]
            elif SECTION_RE.match(title):
                section = section[:1] + [title]
            else:
                section = [title]
            index += 1
            continue

        if line.startswith("**表格 ") or line.startswith("|"):
            flush()
            table_lines = []
            table_page = page
            while index < len(lines):
                current = lines[index].strip()
                if current.startswith("**表格 ") or current.startswith("|"):
                    table_lines.append(current)
                    index += 1
                else:
                    break
            table_md = "\n".join(table_lines)
            for part in split_table(table_md):
                emit(part, table_page, table_page, None, list(section), is_table=True)
            continue

        clause_match = CLAUSE_RE.match(line)
        if clause_mode:
            if clause_match:
                flush()
                buffer_clause = clause_match.group(1)
                append(line.strip())
                index += 1
                continue
            append(line)
            index += 1
            current_text = join_lines(buffer)
            if len(current_text) > 450:
                parts = split_long(current_text)
                for part in parts[:-1]:
                    emit(part, start_page or page, end_page or page, buffer_clause, buffer_section)
                buffer = [parts[-1]]
                start_page = end_page = page
            continue

        append(line)
        index += 1
        current_text = join_lines(buffer)
        if len(current_text) > 450:
            parts = split_long(current_text)
            for part in parts[:-1]:
                emit(part, start_page or page, end_page or page, None, buffer_section)
            buffer = [parts[-1]]
            start_page = end_page = page

    flush()
    return chunks


def load_qa(path: Path):
    chunks = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader):
            question = (row.get("问题") or "").strip()
            answer = (row.get("答案") or "").strip()
            if not question or not answer:
                continue
            text = f"问题：{question}\n答案：{answer}"
            chunks.append(
                {
                    "chunk_id": f"qa-{index:04d}",
                    "doc_id": "qa-csv",
                    "doc_title": "QA知识导入模板",
                    "doc_no": "",
                    "doc_type": "问答",
                    "department": "本科生院",
                    "assistants": ["中传教务君", "问道中传"],
                    "section_path": [],
                    "clause_no": "",
                    "page_start": 0,
                    "page_end": 0,
                    "source_file": path.name,
                    "text": text,
                    "embed_text": text,
                    "is_qa": True,
                    "is_generic": True,
                    "is_table": False,
                    "status": "active",
                }
            )
    return chunks


def embed_and_upsert(chunks, recreate: bool):
    import numpy as np
    from fastembed import TextEmbedding
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PayloadSchemaType, PointStruct, VectorParams

    print(f"loading embedding model: {EMBED_MODEL}", flush=True)
    started = time.time()
    model = TextEmbedding(model_name=EMBED_MODEL, cache_dir=str(FASTEMBED_CACHE))
    print(f"model loaded in {time.time() - started:.1f}s", flush=True)

    texts = [chunk["embed_text"] for chunk in chunks]
    started = time.time()
    vectors = np.vstack(
        [np.asarray(vector, dtype=np.float32) for vector in model.embed(texts, batch_size=32)]
    )
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors / np.maximum(norms, 1e-12)
    print(f"embedded {len(texts)} chunks in {time.time() - started:.1f}s", flush=True)

    client = QdrantClient(
        url=QDRANT_URL,
        api_key=QDRANT_API_KEY or None,
        timeout=120,
    )
    if recreate:
        try:
            client.delete_collection(COLLECTION)
            print(f"deleted collection {COLLECTION}", flush=True)
        except Exception:
            pass

    dim = int(vectors.shape[1])
    if not client.collection_exists(COLLECTION):
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config={"dense": VectorParams(size=dim, distance=Distance.COSINE)},
        )
        print(f"created collection {COLLECTION} (dim={dim})", flush=True)

    for field in ("assistants", "doc_type", "status", "doc_no"):
        try:
            client.create_payload_index(
                collection_name=COLLECTION,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass

    batch_size = 128
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        points = []
        for chunk, vector in zip(batch, vectors[start : start + batch_size]):
            payload = {
                key: value
                for key, value in chunk.items()
                if key not in ("embed_text",)
            }
            points.append(
                PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk["chunk_id"])),
                    vector={"dense": np.asarray(vector, dtype=float).tolist()},
                    payload=payload,
                )
            )
        client.upsert(collection_name=COLLECTION, points=points, wait=True)
        print(f"upserted {min(start + batch_size, len(chunks))}/{len(chunks)}", flush=True)

    count = client.count(COLLECTION, exact=True).count
    print(f"collection {COLLECTION} now has {count} points", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True, help="knowledge docs root")
    parser.add_argument("--qa", default="", help="QA csv path")
    parser.add_argument("--out", default="data")
    parser.add_argument("--include", default="", help="comma separated path substrings")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument("--skip-embed", action="store_true")
    parser.add_argument("--no-tables", action="store_true")
    args = parser.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "md").mkdir(exist_ok=True)

    include = [part.strip() for part in args.include.split(",") if part.strip()]
    pdfs = sorted(src.rglob("*.pdf"))
    if include:
        pdfs = [p for p in pdfs if any(part in str(p.relative_to(src)) for part in include)]
    if args.limit:
        pdfs = pdfs[: args.limit]

    started = time.time()
    records = []
    for pdf in pdfs:
        rel = pdf.relative_to(src)
        assistant = rel.parts[0] if len(rel.parts) > 1 else "shared"
        if args.no_tables:
            original = pymupdf.open(pdf)
            pages = []
            for page in original:
                data = page.get_text("dict")
                lines = []
                for block in data.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        spans = line.get("spans", [])
                        text = "".join(s.get("text", "") for s in spans).strip()
                        if text:
                            lines.append((text, max((s.get("size", 0) for s in spans), default=0)))
                pages.append((lines, []))
            md = render_md(pages)
            plain = "".join(text for lines, _ in pages for text, _ in lines)
            page_count = len(original)
        else:
            md, plain, page_count = extract_pdf(pdf)
        if len(plain) < 100:
            print(f"skip empty: {rel}", flush=True)
            continue
        title = clean_title(pdf.stem)
        doc_no = find_doc_no(title)
        doc_type = classify(title, md)
        record = {
            "doc_id": "d" + hashlib.sha1(str(rel).encode("utf-8")).hexdigest()[:12],
            "title": title,
            "doc_no": doc_no,
            "doc_type": doc_type,
            "department": DEPARTMENT_MAP.get(assistant, assistant),
            "assistants": [assistant],
            "source_file": str(rel),
            "md": md,
            "content_hash": hashlib.sha1(re.sub(r"\s+", "", plain).encode("utf-8")).hexdigest(),
            "pages": page_count,
            "chars": len(plain),
        }
        records.append(record)
        print(f"parsed: {assistant}/{pdf.name} pages={page_count} chars={len(plain)}", flush=True)

    unique = {}
    for record in records:
        existing = unique.get(record["content_hash"])
        if existing is None:
            unique[record["content_hash"]] = record
        else:
            for assistant in record["assistants"]:
                if assistant not in existing["assistants"]:
                    existing["assistants"].append(assistant)
            print(f"dedupe: {record['source_file']} -> {existing['source_file']}", flush=True)

    from difflib import SequenceMatcher

    grouped = {}
    for record in unique.values():
        doc_no = record["doc_no"]
        if doc_no:
            primary = None
            for candidate in grouped.values():
                if (
                    candidate["doc_no"] == doc_no
                    and (
                        core_title(candidate["title"]) == core_title(record["title"])
                        or SequenceMatcher(
                            None,
                            core_title(candidate["title"]),
                            core_title(record["title"]),
                        ).ratio()
                        >= 0.95
                    )
                ):
                    primary = candidate
                    break
            if primary is not None:
                keep, drop = (
                    (record, primary) if record["chars"] > primary["chars"] else (primary, record)
                )
                for assistant in drop["assistants"]:
                    if assistant not in keep["assistants"]:
                        keep["assistants"].append(assistant)
                if keep is record:
                    del grouped[primary["doc_id"]]
                    grouped[record["doc_id"]] = record
                print(
                    f"same doc_no: {drop['source_file']} -> {keep['source_file']}",
                    flush=True,
                )
                continue
        grouped[record["doc_id"]] = record

    docs = sorted(grouped.values(), key=lambda item: (item["assistants"][0], item["title"]))
    apply_assistant_overrides(docs)
    mark_generic(docs)
    all_chunks = []
    for record in docs:
        (out / "md" / f"{record['doc_id']}.md").write_text(record["md"], encoding="utf-8")
        chunks = chunk_document(record)
        all_chunks.extend(chunks)
        print(f"chunked: {record['title']} -> {len(chunks)} chunks", flush=True)

    if args.qa:
        qa_path = Path(args.qa)
        if qa_path.exists():
            qa_chunks = load_qa(qa_path)
            all_chunks.extend(qa_chunks)
            print(f"qa: {len(qa_chunks)} chunks", flush=True)

    def source_rank(title: str):
        is_standalone = 0 if ("汇编" in title or "文件库" in title) else 1
        match = re.search(r"(20\d{2})", title)
        year = int(match.group(1)) if match else 0
        return (is_standalone, year)

    deduped = {}
    for chunk in all_chunks:
        key = hashlib.sha1(re.sub(r"\s+", "", chunk["text"]).encode("utf-8")).hexdigest()
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = chunk
        elif source_rank(chunk["doc_title"]) > source_rank(existing["doc_title"]):
            deduped[key] = chunk
    removed = len(all_chunks) - len(deduped)
    if removed:
        print(f"content dedupe: removed {removed} chunks", flush=True)
    all_chunks = list(deduped.values())

    doc_meta = [
        {
            key: value
            for key, value in record.items()
            if key not in ("md", "content_hash")
        }
        for record in docs
    ]
    (out / "docs.json").write_text(
        json.dumps(doc_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (out / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in all_chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    print(
        f"done parsing: {len(docs)} docs, {len(all_chunks)} chunks, "
        f"{time.time() - started:.1f}s",
        flush=True,
    )
    if not args.skip_embed:
        if not all_chunks:
            raise SystemExit("no chunks to embed")
        embed_and_upsert(all_chunks, recreate=args.recreate)


if __name__ == "__main__":
    main()
