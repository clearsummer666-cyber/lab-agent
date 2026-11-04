"""运行 A/B/C/D 四组真实检索与生成，保存逐例结果及汇总指标。"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from eval_suite.bootstrap import configure_eval_environment

configure_eval_environment()

from app.database import SessionLocal
from app.services.kb_service import query_rules
from eval_suite.fixtures import load_benchmark, reset_test_environment
from eval_suite.scorers import keyword_coverage, reciprocal_rank

REPORT_DIR = Path(__file__).with_name("reports")


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def run_rag_ablation() -> dict:
    cases = load_benchmark()["rag_test_cases"]
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "retrieval": "local deterministic hashed embedding + jieba/BM25 + RRF(k=60)",
              "case_count": len(cases), "strategies": {}}
    with SessionLocal() as db:
        for strategy in "ABCD":
            reset_test_environment(db)
            results = []
            for case in cases:
                output = query_rules(case["question"], strategy=strategy,
                                     lab_type=case["lab_type"], user_role=case["user_role"],
                                     effective_date=case["effective_date"])
                expected = case["expected_chunk_ids"]
                citations = output["citations"]
                if strategy == "A":
                    # 固定块以 article__fixed_NN 保留源映射，评分仍按条款真值。
                    citations = [item.split("__fixed_")[0] for item in citations]
                results.append({
                    "case_id": case["case_id"], "citations": citations,
                    "rule_codes": output["rule_codes"], "answer": output["answer"],
                    "parent_context_count": len(output["parent_contexts"]),
                    "hit_at_3": float(bool(set(expected) & set(citations))),
                    "mrr": reciprocal_rank(citations, expected),
                    "top_1_hit": float(bool(expected and citations and citations[0] == expected[0])),
                    "expected_recall_at_3": _mean([float(item in citations) for item in expected]) if expected else None,
                    "expired_recall": float(bool(set(case["forbidden_chunk_ids"]) & set(citations)
                                                 & {"chem_rules_2025_expired_art_03"})),
                    "condition_coverage": keyword_coverage(output["answer"], case["required_answer_keywords"]),
                    "rule_code_coverage": _mean([float(code in output["rule_codes"]) for code in case["expected_rule_codes"]])
                    if case["expected_rule_codes"] else None,
                    "abstention_correct": (not case.get("expect_evidence_sufficient", True)
                                            and not output["evidence_sufficient"]),
                })
            metrics = {
                "hit_rate_at_3": _mean([r["hit_at_3"] for r in results]),
                "mrr": _mean([r["mrr"] for r in results]),
                "top_1_hit_rate": _mean([r["top_1_hit"] for r in results]),
                "expired_rule_recall_rate": _mean([r["expired_recall"] for r in results]),
                "key_condition_coverage": _mean([r["condition_coverage"] for r in results]),
                "expected_chunk_recall_at_3": _mean([r["expected_recall_at_3"] for r in results
                                                       if r["expected_recall_at_3"] is not None]),
                "rule_code_coverage": _mean([r["rule_code_coverage"] for r in results
                                               if r["rule_code_coverage"] is not None]),
            }
            report["strategies"][strategy] = {"metrics": metrics, "cases": results}
    return report


def markdown(report: dict) -> str:
    labels = {"hit_rate_at_3": "Hit Rate@3", "mrr": "MRR", "top_1_hit_rate": "Top-1 命中率",
              "expired_rule_recall_rate": "过期规则误召回率 ↓",
              "key_condition_coverage": "关键条件覆盖率",
              "expected_chunk_recall_at_3": "期望条款召回率@3（辅助）",
              "rule_code_coverage": "静态规则码覆盖率（辅助）"}
    lines = ["# RAG 四组消融实测报告", "", f"用例数：{report['case_count']}；RRF：k=60；Top-k：3。",
             "检索实现：本地特征哈希向量、jieba + rank_bm25，无外部模型调用。",
             "Hit/MRR/Top-1/过期率/条件覆盖率以 6 例为分母；条款/规则码覆盖率以 5 个有标准答案用例为分母。", "",
             "| 指标 | A 固定块+向量 | B 条款+混合RRF | C +元数据过滤 | D +重排+父块 |",
             "|---|---:|---:|---:|---:|"]
    for key, label in labels.items():
        values = [report["strategies"][s]["metrics"][key] for s in "ABCD"]
        formatted = [f"{v:.4f}" for v in values] if key == "mrr" else [f"{v * 100:.2f}%" for v in values]
        lines.append(f"| {label} | " + " | ".join(formatted) + " |")
    lines.extend(["", "## 逐例命中与覆盖率", "", "| 策略 | 用例 | 返回 chunk_id | 条件覆盖率 |",
                  "|---|---|---|---:|"])
    for strategy in "ABCD":
        for result in report["strategies"][strategy]["cases"]:
            lines.append(f"| {strategy} | {result['case_id']} | {', '.join(result['citations']) or '—'} | {result['condition_coverage'] * 100:.2f}% |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    REPORT_DIR.mkdir(exist_ok=True)
    report = run_rag_ablation()
    (REPORT_DIR / "rag_ablation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    text = markdown(report)
    (REPORT_DIR / "rag_ablation_report.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
