import logging
import re
from typing import TYPE_CHECKING

from app.config import BASE_DIR

if TYPE_CHECKING:
    # 仅供类型检查器用；运行时不导入，保证 `import app.main` 不拖 chromadb/torch 栈
    from chromadb.api.models.Collection import Collection

logger = logging.getLogger(__name__)

KB_DIR = BASE_DIR / "data" / "kb"
CHROMA_DIR = BASE_DIR / "data" / "chroma"

# ── 检索参数：cosine 距离 ∈ [0,2]，similarity = 1 - dist ∈ [-1,1] ──
SEARCH_TOP_K = 8            # 先召回 top 8
SEARCH_FINAL_K = 3          # 过滤后按分排序取前 3
SIMILARITY_THRESHOLD = 0.3  # similarity 低于该值的块直接丢弃
MAX_CHUNK_CHARS = 500       # 单块上限（含拼在开头的标题行）

_HEADING_RE = re.compile(r"^#{1,3}\s+\S.*$")

_collection = None

_embedding_fn = None


def split_markdown(text: str, source: str) -> list[dict]:
    """把整篇 Markdown 按标题行切成带上下文的块（纯函数，不依赖模型与向量库）。

    规则：#/##/### 标题行开启新块；单块超 500 字再按空行段落切，每块 ≤500 字；
    每块开头拼上所属标题行作为上下文；heading 元数据存去掉 # 号的标题文字。
    返回 [{"content":..., "metadata":{"source":..., "chunk_index":int, "heading":...}}]。
    """
    if not text or not text.strip():
        return []

    sections: list[tuple[str, str]] = []  # (标题行, 正文)
    heading = ""
    body_lines: list[str] = []
    for line in text.strip().splitlines():
        stripped = line.strip()
        if _HEADING_RE.match(stripped):
            if heading or "".join(body_lines).strip():
                sections.append((heading, "\n".join(body_lines).strip()))
            heading = stripped
            body_lines = []
        else:
            body_lines.append(line)
    sections.append((heading, "\n".join(body_lines).strip()))

    chunks: list[dict] = []
    for heading, body in sections:
        if heading:
            budget = MAX_CHUNK_CHARS - len(heading) - 1  # 预留给 "标题\n"
            if budget <= 0:
                budget = MAX_CHUNK_CHARS
            pieces = (
                [heading]
                if not body
                else [f"{heading}\n{p}" for p in _split_by_size(body, budget)]
            )
        else:
            pieces = _split_by_size(body, MAX_CHUNK_CHARS) if body else []
        for piece in pieces:
            chunks.append(
                {
                    "content": piece,
                    "metadata": {
                        "source": source,
                        "chunk_index": len(chunks),
                        "heading": _heading_text(heading),
                    },
                }
            )
    return chunks


def _heading_text(heading_line: str) -> str:
    """'## 取消规则' -> '取消规则'；无标题行返回空串。"""
    return re.sub(r"^#{1,3}\s+", "", heading_line) if heading_line else ""


def _split_by_size(text: str, limit: int) -> list[str]:
    """按空行段落切分并贪心打包；单段超限时硬切成 ≤limit 的片段。"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    buf = ""
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


def get_embedding_fn():
    """懒加载 embedding 模型：只加载一次，之后复用同一个实例"""
    global _embedding_fn
    if _embedding_fn is not None:
        return _embedding_fn
    from chromadb.utils import embedding_functions  # 函数体内导入，避免模块加载即拖 torch

    # SentenceTransformerEmbeddingFunction 自带类级缓存，同名模型也不会重复加载
    _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="BAAI/bge-small-zh-v1.5"
    )
    return _embedding_fn


def _load_kb_chunks() -> list[dict]:
    """读 data/kb 全部 md 并切成块，作为向量库的统一入库数据源。"""
    chunks: list[dict] = []
    for path in sorted(KB_DIR.glob("*.md")):
        text = path.read_text(encoding="UTF-8").strip()
        if text:
            chunks.extend(split_markdown(text, path.name))
    return chunks


def get_collection() -> "Collection":
    """向量库初始化（懒加载；cosine 空间；与磁盘分块不一致时自动重建）"""
    global _collection

    if _collection is not None:
        return _collection

    import chromadb  # 函数体内导入，避免模块加载即拖 chromadb/torch 栈

    KB_DIR.mkdir(parents=True, exist_ok=True)
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    col = client.get_or_create_collection(
        name="lab_kb",
        metadata={"hnsw:space": "cosine"},
        embedding_function=get_embedding_fn(),
    )
    chunks = _load_kb_chunks()
    # 平滑升级：chromadb 对已存在的 collection 不会应用新 metadata，旧库可能是
    # 「4 篇整文档 + L2 空间」。所以空间不对或条数与当前分块不符时，整库删除重建。
    col_space = (getattr(col, "metadata", None) or {}).get("hnsw:space")
    if col_space != "cosine" or col.count() != len(chunks):
        client.delete_collection("lab_kb")
        col = client.get_or_create_collection(
            name="lab_kb",
            metadata={"hnsw:space": "cosine"},
            embedding_function=get_embedding_fn(),
        )
        if chunks:
            col.add(
                ids=[
                    f"{c['metadata']['source']}#{c['metadata']['chunk_index']}"
                    for c in chunks
                ],
                documents=[c["content"] for c in chunks],
                metadatas=[c["metadata"] for c in chunks],
            )
    _collection = col
    return col


def search(query: str):
    """根据关键字检索向量库，返回拼好的上下文串；无命中返回空串。

    格式：[{source}#{chunk_index} {heading}]\\n{content}，块之间用空行分隔。
    """
    col = get_collection()
    if col.count() == 0:
        return ""
    limit = min(SEARCH_TOP_K, col.count())
    res = col.query(query_texts=[query], n_results=limit)
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    distances = (res.get("distances") or [[]])[0]

    scored = []
    for doc, meta, dist in zip(docs, metas, distances):
        # cosine 距离 ∈ [0,2]；similarity = 1 - dist，越大越相关
        similarity = 1 - dist
        if similarity < SIMILARITY_THRESHOLD:
            continue
        meta = meta or {}
        source = meta.get("source") or ""
        heading = meta.get("heading") or ""
        chunk_index = meta.get("chunk_index", 0)
        scored.append(
            {
                "score": similarity,
                "content": f"[{source}#{chunk_index} {heading}]\n{doc}",
            }
        )
    logger.debug("检索结果：%s", [(s["score"], s["content"][:40]) for s in scored])
    scored.sort(key=lambda x: x["score"], reverse=True)
    final_parts = [item["content"] for item in scored[:SEARCH_FINAL_K]]
    return "\n\n".join(final_parts)


def warmup():
    """启动时预热：把模型加载 + 首次检索的开销付在启动阶段，别落在第一个用户请求上"""
    col = get_collection()
    if col.count() > 0:
        col.query(query_texts=["预热"], n_results=1)
