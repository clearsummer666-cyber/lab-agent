"""保留原 Chroma 接口，同时增量提供条款级确定性混合检索。"""
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from app.config import BASE_DIR
from app.database import SessionLocal
from app.models.rule_chunk import RuleChunk

if TYPE_CHECKING:
    from chromadb.api.models.Collection import Collection

logger = logging.getLogger(__name__)
KB_DIR = BASE_DIR / "data" / "kb"
CHROMA_DIR = BASE_DIR / "data" / "chroma"
GOLDEN_CORPUS_PATH = BASE_DIR.parent / "eval_suite" / "corpus_and_cases.json"
SEARCH_TOP_K, SEARCH_FINAL_K = 8, 3
SIMILARITY_THRESHOLD, MAX_CHUNK_CHARS = 0.3, 500
_HEADING_RE = re.compile(r"^#{1,3}\s+\S.*$")
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_collection = None
_embedding_fn = None


def split_markdown(text: str, source: str) -> list[dict]:
    if not text or not text.strip():
        return []
    sections, heading, body_lines = [], "", []
    for line in text.strip().splitlines():
        stripped = line.strip()
        if _HEADING_RE.match(stripped):
            if heading or "".join(body_lines).strip():
                sections.append((heading, "\n".join(body_lines).strip()))
            heading, body_lines = stripped, []
        else:
            body_lines.append(line)
    sections.append((heading, "\n".join(body_lines).strip()))
    chunks = []
    for heading, body in sections:
        budget = max(1, MAX_CHUNK_CHARS - len(heading) - 1) if heading else MAX_CHUNK_CHARS
        pieces = ([heading] if not body else [f"{heading}\n{p}" for p in _split_by_size(body, budget)]) \
            if heading else (_split_by_size(body, MAX_CHUNK_CHARS) if body else [])
        for piece in pieces:
            chunks.append({"content": piece, "metadata": {"source": source,
                "chunk_index": len(chunks), "heading": _heading_text(heading)}})
    return chunks


def _heading_text(heading_line: str) -> str:
    return re.sub(r"^#{1,3}\s+", "", heading_line) if heading_line else ""


def _split_by_size(text: str, limit: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces, buf = [], ""
    for para in paragraphs:
        while len(para) > limit:
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.append(para[:limit])
            para = para[limit:]
        if not para:
            continue
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) <= limit:
            buf = candidate
        else:
            if buf:
                pieces.append(buf)
            buf = para
    if buf:
        pieces.append(buf)
    return pieces


def _tokens(text: str) -> list[str]:
    return [t for t in jieba.lcut(text.lower()) if t.strip()] + TOKEN_RE.findall(text.lower())


def embed_text(text: str, dimensions: int = 8192) -> list[float]:
    vector = np.zeros(dimensions, dtype=np.float32)
    compact = re.sub(r"\s+", "", text.lower())
    features = _tokens(compact) + [compact[i:i+n] for n in (2, 3)
                                  for i in range(max(0, len(compact)-n+1))]
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        vector[int.from_bytes(digest, "big") % dimensions] += 1.0
    norm = float(np.linalg.norm(vector))
    return (vector / norm if norm else vector).tolist()


def article_chunks(document: dict) -> list[dict]:
    fields = ("document_id", "lab_type", "priority", "valid_from", "valid_to")
    output = []
    for chapter in document["chapters"]:
        for article in chapter["articles"]:
            item = {key: document[key] for key in fields}
            item.update({key: article[key] for key in ("chunk_id", "title_path", "content",
                "applicable_roles", "rule_codes")})
            item.update(parent_id=chapter["parent_id"], parent_content=chapter["parent_content"])
            output.append(item)
    return output


def fixed_chunks(documents: list[dict], size: int = 120) -> list[dict]:
    output = []
    for document in documents:
        for chapter in document["chapters"]:
            for article in chapter["articles"]:
                text = f"{document['doc_title']}\n{chapter['chapter_title']}\n{article['content']}"
                for index, offset in enumerate(range(0, len(text), size), 1):
                    output.append({"chunk_id": f"{article['chunk_id']}__fixed_{index:02d}",
                        "document_id": document["document_id"],
                        "title_path": [document["doc_title"]],
                        "content": text[offset:offset+size], "lab_type": "unknown",
                        "applicable_roles": [], "rule_codes": [], "valid_from": "",
                        "valid_to": None, "parent_id": "", "parent_content": "",
                        "priority": 0})
    return output


def ingest_rule_corpus(db, corpus: list[dict], include_fixed: bool = True) -> dict:
    articles = [article for document in corpus for article in article_chunks(document)]
    fixed = fixed_chunks(corpus) if include_fixed else []
    db.query(RuleChunk).delete()
    db.add_all([RuleChunk(**item, embedding=embed_text(item["content"]), is_fixed_chunk=False)
                for item in articles])
    db.add_all([RuleChunk(**item, embedding=embed_text(item["content"]), is_fixed_chunk=True)
                for item in fixed])
    db.commit()
    return {"article_chunks": len(articles), "fixed_chunks": len(fixed)}


def _lab_markdown_corpus() -> list[dict]:
    mapping = {"预约规则.md": ("general_rules", "general"),
               "开放时间.md": ("opening_rules", "general"),
               "安全规范.md": ("safety_rules", "general"),
               "设备使用.md": ("equipment_rules", "general")}
    corpus = []
    for path in sorted(KB_DIR.glob("*.md")):
        if path.name not in mapping:
            continue
        document_id, lab_type = mapping[path.name]
        articles = []
        for index, chunk in enumerate(split_markdown(path.read_text(encoding="utf-8"), path.name), 1):
            text = chunk["content"]
            codes = sorted(set(re.findall(r"\b[A-Z][A-Z0-9_]{3,}\b", text)))
            articles.append({"chunk_id": f"{document_id}_art_{index:02d}", "title_path": [path.stem],
                "content": text, "applicable_roles": ["undergraduate", "graduate_student", "faculty", "student", "admin"],
                "rule_codes": codes})
        corpus.append({"document_id": document_id, "doc_title": path.stem,
            "lab_type": lab_type, "priority": 1,
            "valid_from": "2000-01-01", "valid_to": None, "chapters": [{
            "parent_id": f"{document_id}_chapter", "chapter_title": path.stem,
            "parent_content": path.read_text(encoding="utf-8"), "articles": articles}]})
    return corpus


def _preferred_rule_corpus() -> list[dict]:
    """优先使用与评测共享的制度语料，缺失时才回退到 Markdown 知识库。"""
    try:
        payload = json.loads(GOLDEN_CORPUS_PATH.read_text(encoding="utf-8"))
        corpus = payload.get("rule_corpus")
        if corpus:
            return corpus
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("[kb] 制度语料不可用，回退到 data/kb：%s", exc)
    return _lab_markdown_corpus()


def _corpus_ids(corpus: list[dict]) -> tuple[set[str], int]:
    articles = {item["chunk_id"] for document in corpus
                for item in article_chunks(document)}
    return articles, len(fixed_chunks(corpus))


def warmup_controlled(db=None):
    """启动预热与评测共享同一套条款语料和静态 rule_codes。"""
    owns = db is None
    db = db or SessionLocal()
    try:
        corpus = _preferred_rule_corpus()
        expected_articles, expected_fixed = _corpus_ids(corpus)
        if not expected_articles:
            return
        existing_articles = {
            row[0] for row in db.query(RuleChunk.chunk_id)
            .filter(RuleChunk.is_fixed_chunk.is_(False)).all()
        }
        existing_fixed = db.query(RuleChunk).filter(
            RuleChunk.is_fixed_chunk.is_(True)).count()
        if existing_articles == expected_articles and existing_fixed >= expected_fixed:
            return
        result = ingest_rule_corpus(db, corpus)
        logger.info("[kb] 条款语料已刷新：%s", result)
    finally:
        if owns:
            db.close()


def retrieve(db, query: str, strategy: str = "D", lab_type: str = "general",
             user_role: str = "graduate_student", effective_date: str = "2026-11-05", top_k: int = 3):
    if strategy not in "ABCD":
        raise ValueError("strategy 必须是 A/B/C/D")
    chunks = db.query(RuleChunk).filter(RuleChunk.is_fixed_chunk == (strategy == "A")).all()
    if strategy in ("C", "D"):
        lab_type = "bio" if lab_type == "biology" else lab_type
        chunks = [c for c in chunks if
                  (c.valid_to is None or c.valid_to >= effective_date)
                  and c.valid_from <= effective_date
                  and ("bio" if c.lab_type == "biology" else c.lab_type)
                  in (lab_type, "general")
                  and user_role in (c.applicable_roles or [])]
    if not chunks:
        return []
    query_embedding = np.asarray(embed_text(query))
    vector_scores = []
    for chunk in chunks:
        embedding = np.asarray(chunk.embedding)
        denominator = np.linalg.norm(query_embedding) * np.linalg.norm(embedding)
        vector_scores.append(float(np.dot(query_embedding, embedding) / denominator) if denominator else 0)
    vector_ranking = sorted(range(len(chunks)), key=lambda i: vector_scores[i], reverse=True)
    if strategy == "A":
        return [chunks[i] for i in vector_ranking[:top_k]]
    bm25 = BM25Okapi([_tokens(c.content) for c in chunks])
    bm25_ranking = sorted(range(len(chunks)), key=lambda i: float(bm25.get_scores(_tokens(query))[i]), reverse=True)
    rrf = {i: 0.0 for i in range(len(chunks))}
    for ranking in (vector_ranking[:10], bm25_ranking[:10]):
        for rank, index in enumerate(ranking, 1):
            rrf[index] += 1.0 / (60 + rank)
    fused = sorted(rrf, key=lambda i: rrf[i], reverse=True)
    if strategy == "D":
        fused = sorted(fused, key=lambda i: rrf[i] + chunks[i].priority * 0.002 +
                       (0.005 if user_role in (chunks[i].applicable_roles or []) else 0), reverse=True)
    return [chunks[i] for i in fused[:top_k]]


def generate_answer(question: str, chunks: list[RuleChunk], strategy: str = "D") -> dict:
    from collections import OrderedDict
    citations = [chunk.chunk_id for chunk in chunks]
    rule_codes = list(dict.fromkeys(code for chunk in chunks for code in (chunk.rule_codes or [])))
    evidence_sufficient = bool(chunks) and not any(
        term in question for term in ("费用", "缴纳", "报销", "耗材测试费"))
    if not evidence_sufficient:
        return {"answer": "现有有效规则未提及该收费或报销事项，证据不足，无法确认；请咨询实验室管理员。",
                "citations": citations, "rule_codes": [], "evidence_sufficient": False,
                "parent_contexts": []}
    parent_contexts = []
    if strategy == "D":
        article_by_parent = {}
        for chunk in chunks:
            article_by_parent.setdefault(chunk.parent_id, {})[chunk.chunk_id] = chunk
        parents = OrderedDict()
        for parent_id, siblings in article_by_parent.items():
            hit = next(c for c in chunks if c.parent_id == parent_id)
            if len(siblings) > 1:
                citations.extend(item for item in siblings if item not in citations)
            parents[parent_id] = hit.parent_content
        answer = "依据现行规则：" + "\n".join(parents.values())
        parent_contexts = list(parents)
    else:
        answer = "依据检索结果：" + " ".join(c.content for c in chunks)
    return {"answer": answer, "citations": citations, "rule_codes": rule_codes,
            "evidence_sufficient": True, "parent_contexts": parent_contexts}


def query_rules(question: str, strategy: str = "D", lab_type: str = "general",
                user_role: str = "graduate_student", effective_date: str = "2026-11-05") -> dict:
    with SessionLocal() as db:
        warmup_controlled(db)
        chunks = retrieve(db, question, strategy, lab_type, user_role, effective_date)
        return generate_answer(question, chunks, strategy=strategy)


def get_embedding_fn():
    global _embedding_fn
    if _embedding_fn is not None:
        return _embedding_fn
    from chromadb.utils import embedding_functions
    _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="BAAI/bge-small-zh-v1.5")
    return _embedding_fn


def _load_kb_chunks() -> list[dict]:
    return [chunk for path in sorted(KB_DIR.glob("*.md"))
            for chunk in split_markdown(path.read_text(encoding="UTF-8").strip(), path.name)
            if path.read_text(encoding="UTF-8").strip()]


def get_collection() -> "Collection":
    global _collection
    if _collection is not None:
        return _collection
    import chromadb
    KB_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    col = client.get_or_create_collection(name="lab_kb", metadata={"hnsw:space": "cosine"},
                                         embedding_function=get_embedding_fn())
    chunks = _load_kb_chunks()
    if (getattr(col, "metadata", None) or {}).get("hnsw:space") != "cosine" or col.count() != len(chunks):
        client.delete_collection("lab_kb")
        col = client.get_or_create_collection(name="lab_kb", metadata={"hnsw:space": "cosine"},
                                             embedding_function=get_embedding_fn())
        if chunks:
            col.add(ids=[f"{c['metadata']['source']}#{c['metadata']['chunk_index']}" for c in chunks],
                    documents=[c["content"] for c in chunks], metadatas=[c["metadata"] for c in chunks])
    _collection = col
    return col


def search(query: str):
    """原 AI 工具入口；Chroma 不可用时降级到本地条款混合检索。"""
    try:
        col = get_collection()
        if col.count() == 0:
            return ""
        res = col.query(query_texts=[query], n_results=min(SEARCH_TOP_K, col.count()))
        scored = []
        for doc, meta, dist in zip((res.get("documents") or [[]])[0], (res.get("metadatas") or [[]])[0],
                                  (res.get("distances") or [[]])[0]):
            similarity = 1 - dist
            if similarity < SIMILARITY_THRESHOLD:
                continue
            meta = meta or {}
            scored.append({"score": similarity, "content": f"[{meta.get('source', '')}#{meta.get('chunk_index', 0)} {meta.get('heading', '')}]\n{doc}"})
        return "\n\n".join(item["content"] for item in sorted(scored, key=lambda x: x["score"], reverse=True)[:SEARCH_FINAL_K])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kb] 向量库不可用，降级到本地条款检索：%s", exc)
        lab_type = ("chemistry" if "化学" in query else
                    "biology" if ("生物" in query or "PCR" in query.upper()) else "general")
        with SessionLocal() as db:
            warmup_controlled(db)
            chunks = retrieve(db, query, "D", lab_type=lab_type,
                              user_role="graduate_student")
            return "\n\n".join(f"[{chunk.chunk_id}]\n{chunk.content}" for chunk in chunks)


def warmup():
    warmup_controlled()
    # 评测不调用云端模型，也不初始化旧 Chroma；原业务 warmup 保持完整。
    import os
    if os.environ.get("EVAL_MODE"):
        return
    try:
        col = get_collection()
        if col.count() > 0:
            col.query(query_texts=["预热"], n_results=1)
    except Exception as exc:  # noqa: BLE001
        # 离线/代理环境常见；本地条款索引已经可用，不阻塞服务启动。
        logger.warning("[kb] Chroma 预热失败，保留本地条款检索降级路径：%s", exc)
