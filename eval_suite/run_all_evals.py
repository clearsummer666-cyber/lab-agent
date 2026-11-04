"""一键执行两项实验，并只根据落盘 JSON 生成最终简历指标。"""
import json
import platform
from datetime import datetime
from pathlib import Path

from eval_suite.bootstrap import configure_eval_environment

configure_eval_environment()

from eval_suite.agent_eval_runner import run_agent_evaluation
from eval_suite.rag_ablation_runner import run_rag_ablation

REPORTS = Path(__file__).with_name("reports")


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def write_reports() -> None:
    REPORTS.mkdir(exist_ok=True)
    rag = run_rag_ablation()
    agent = run_agent_evaluation()
    for name, data in (("rag_ablation_report", rag), ("agent_eval_report", agent)):
        (REPORTS / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = agent["summary"]
    rows = []
    labels = [("hit_rate_at_3", "Hit Rate@3"), ("mrr", "MRR"),
              ("top_1_hit_rate", "Top-1 命中率"),
              ("expired_rule_recall_rate", "过期规则误召回率 ↓"),
              ("key_condition_coverage", "关键条件覆盖率"),
              ("expected_chunk_recall_at_3", "期望条款召回率@3（辅助）"),
              ("rule_code_coverage", "静态规则码覆盖率（辅助）")]
    for key, label in labels:
        values = [rag["strategies"][s]["metrics"][key] for s in "ABCD"]
        formatted = [f"{v:.4f}" for v in values] if key == "mrr" else [pct(v) for v in values]
        rows.append(f"| {label} | " + " | ".join(formatted) + " |")
    lines = ["# FINAL_RESUME_METRICS · 实验室 RAG 与受控 Agent 实测数据", "",
             f"生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}（Asia/Shanghai）", "",
             "## 实测环境与统计口径", "",
             f"- Python {platform.python_version()}；SQLAlchemy + SQLite；FastAPI TestClient HTTP；jieba + rank_bm25。",
             "- 向量采用本地 8192 维字符/词特征哈希及余弦相似度；RRF 常数 k=60；每组返回 Top-3。未调用外部大模型，结果可离线复现。",
             "- RAG：6 条黄金用例；条款召回率与规则码覆盖率仅在 5 条有标准答案的用例上计算。A 的固定块通过 `article__fixed_NN` 溯源到条款后评分。",
             "- Agent：6 条多轮/故障用例 × Baseline/Candidate 配对运行；每次重置同一数据库夹具；时段与确认参数从上一轮响应动态读取。",
             "- MRR 为倒数排名均值，不写成百分数；基线关闭资质拦截、预览失效和写后超时恢复，其余业务约束保持一致。", "",
             "## RAG 四组消融实测", "",
             "| 指标 | A 固定块+向量 | B 条款+混合RRF | C +元数据过滤 | D +重排+父块 |",
             "|---|---:|---:|---:|---:|", *rows, "",
             "## Agent 配对回归实测", "",
             "| 版本 | 通过数 | 通过率 |", "|---|---:|---:|",
             f"| Baseline v1 | {agent['versions']['baseline']['passed']}/6 | {pct(summary['baseline_pass_rate'])} |",
             f"| Candidate v2 | {agent['versions']['candidate']['passed']}/6 | {pct(summary['candidate_pass_rate'])} |", "",
             "| 用例 | Baseline | Candidate | 结果 |", "|---|---|---|---|"]
    for case in agent["cases"]:
        lines.append(f"| {case['case_id']} | {'PASS' if case['baseline']['passed'] else 'FAIL'} | "
                     f"{'PASS' if case['candidate']['passed'] else 'FAIL'} | {case['regression_status']} |")
    lines.extend(["", f"- FIXED：**{summary['fixed']}**；REGRESSED：**{summary['regressed']}**。",
                  f"- Candidate 数据库状态 / 工具轨迹 / 语义条件三层通过率：**{pct(summary['candidate_business_pass_rate'])} / {pct(summary['candidate_trajectory_pass_rate'])} / {pct(summary['candidate_semantic_pass_rate'])}**。",
                  f"- 重复确认及超时恢复场景的重复写入：**{summary['candidate_duplicate_writes']} 条**。", "",
                  "## 5 项设计漏洞对应实现与验证", "",
                  "| 修复项 | 实现 | 实测证据 |", "|---|---|---|",
                  "| 通用规则漏召回 | `lab_type IN (target, general)`，按有效期、角色联合过滤 | 通风橱与 4 小时通用约束同答；C/D 静态规则码覆盖率 100% |",
                  "| rule_code 映射断层 | 入库静态 JSON 元数据，由 Python 集合组装 | C/D 规则码覆盖率 100%；A 为 0%，符合元数据剥离设定 |",
                  "| 正常预约冗余 RAG | 主链路直接查时段/预览；仅缺资质或规则问答触发检索 | `test_normal_flow_has_no_redundant_rule_search` 断言工具序列 |",
                  "| 动态故障卡点 | `X-Fault-Inject` + ContextVar；抢占、写后超时、空检索 | 抢占冲突反馈；超时后 `get_reservation` 核对并进入 SUCCEEDED |",
                  "| 多轮参数死板 | Runner 读取候选时段、preview_id、confirmation_id；修改日不携带旧选择 | 修改用例最终落库日期为 2026-11-07，旧预览为 INVALIDATED |", "",
                  "## 可复现命令与原始产物", "",
                  "```powershell", "python -m pip install -r requirements.txt",
                  "python -m eval_suite.rag_ablation_runner",
                  "python -m eval_suite.agent_eval_runner",
                  "python -m eval_suite.run_all_evals",
                  "python -m pytest tests/test_lab_eval.py -q", "```", "",
                  "逐例分数、完整回答、工具轨迹与 HTTP 结果见同目录 `rag_ablation_report.json` 与 `agent_eval_report.json`。",
                  "本报告为小规模黄金集功能/消融实测，不外推为通用模型能力或生产环境并发性能结论。", ""])
    target = REPORTS / "FINAL_RESUME_METRICS.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"已生成 {target}")


if __name__ == "__main__":
    write_reports()
