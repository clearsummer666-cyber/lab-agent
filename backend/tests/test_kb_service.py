"""kb_service.split_markdown 纯函数测试。

只测切块逻辑，不依赖真实 data/kb 与 embedding 模型
（kb_service 模块本身已无顶层 chromadb/torch 导入，import 即安全）。
"""

from app.services.kb_service import MAX_CHUNK_CHARS, split_markdown


def test_split_by_heading():
    """按标题行切成两块，chunk_index 连续，content 带标题上下文。"""
    text = "# 预约规则\n上午内容\n## 开放时间\n九点到五点"
    chunks = split_markdown(text, "预约规则.md")
    assert len(chunks) == 2
    assert chunks[0]["metadata"]["chunk_index"] == 0
    assert chunks[1]["metadata"]["chunk_index"] == 1
    assert chunks[0]["content"].startswith("# 预约规则\n")
    assert chunks[1]["metadata"]["source"] == "预约规则.md"


def test_metadata_keys_exact():
    """metadata 必须且只含 source / chunk_index / heading 三个键。"""
    chunks = split_markdown("# 标题\n正文", "a.md")
    assert chunks[0]["metadata"] == {
        "source": "a.md",
        "chunk_index": 0,
        "heading": "标题",
    }


def test_long_section_split_by_paragraph():
    """单节超过 500 字时按空行段落再切，且每块仍 ≤500 字。"""
    paras = [f"段落{i}，" + "字" * 300 for i in range(3)]  # 各 ~304 字，合计 > 500
    text = "# 安全规范\n\n" + "\n\n".join(paras)
    chunks = split_markdown(text, "安全规范.md")
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c["content"]) <= MAX_CHUNK_CHARS
        assert c["content"].startswith("# 安全规范\n")


def test_heading_carried_in_metadata_and_content():
    """heading 元数据存去 # 后的标题文字，content 开头拼原始标题行。"""
    chunks = split_markdown("## 取消规则\n活动开始前两小时可取消。", "a.md")
    assert chunks[0]["metadata"]["heading"] == "取消规则"
    assert chunks[0]["content"].startswith("## 取消规则\n")


def test_empty_text_returns_empty_list():
    """空文本 / 纯空白文本都返回 []。"""
    assert split_markdown("", "a.md") == []
    assert split_markdown("   \n  \t ", "a.md") == []


def test_chunk_size_cap_hard_split():
    """无空行的超长段落被硬切，所有块 ≤500 字，正文无丢失式报错。"""
    text = "# 长文\n" + "很" * 2000
    chunks = split_markdown(text, "long.md")
    assert len(chunks) > 1
    for c in chunks:
        assert len(c["content"]) <= MAX_CHUNK_CHARS


def test_multi_level_headings():
    """#/##/### 三级标题各成一块，heading 依次携带。"""
    text = "# 一级\n甲\n## 二级\n乙\n### 三级\n丙"
    chunks = split_markdown(text, "m.md")
    assert len(chunks) == 3
    assert [c["metadata"]["heading"] for c in chunks] == ["一级", "二级", "三级"]


def test_text_before_first_heading_kept():
    """首个标题前的正文也要保留为独立块（heading 为空串）。"""
    chunks = split_markdown("前言介绍\n# 正文标题\n正文", "a.md")
    assert len(chunks) == 2
    assert chunks[0]["metadata"]["heading"] == ""
    assert chunks[0]["content"] == "前言介绍"
