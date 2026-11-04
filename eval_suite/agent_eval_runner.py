"""通过原项目 /api/agent/chat 执行真实多轮与故障配对回归。"""
import json
from datetime import datetime, timezone
from pathlib import Path

from eval_suite.bootstrap import configure_eval_environment

configure_eval_environment()

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models.agent_run import AgentRun
from eval_suite.fixtures import load_benchmark
from eval_suite.scorers import BusinessStateScorer, SemanticConditionScorer, TrajectoryScorer

REPORT_DIR = Path(__file__).with_name("reports")


def run_agent_evaluation() -> dict:
    cases = load_benchmark()["agent_test_cases"]
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "transport": "FastAPI TestClient HTTP + X-Fault-Inject + isolated SQLite",
              "case_count": len(cases), "cases": [], "versions": {}}
    with TestClient(app) as client:
        for mode in ("baseline", "candidate"):
            report["versions"][mode] = {"passed": 0, "failed": 0}
        for case in cases:
            pair = {"case_id": case["case_id"], "baseline": None, "candidate": None}
            for mode in ("baseline", "candidate"):
                reset = client.post("/api/test/environment/reset")
                reset.raise_for_status()
                fixture = reset.json()["data"]
                user_id = fixture["user_ids"][case["user_id"]]
                session_id = f"eval-{mode}-{case['case_id']}"
                headers = {"X-Test-User-Id": str(user_id)}
                if case.get("fault_inject"):
                    headers["X-Fault-Inject"] = case["fault_inject"]
                answers, responses, trace_ids, preview_context = [], [], [], {}
                for turn in case["turns"]:
                    payload = {"session_id": session_id, "user_id": user_id,
                               "message": turn["user"],
                               "force_confirm_attempt": turn.get("action") == "FORCE_CONFIRM_ATTEMPT",
                               "mode": mode}
                    previous = responses[-1] if responses else {}
                    structured = previous.get("structured_data", {})
                    candidates = structured.get("candidate_slots", [])
                    if candidates and (turn.get("action") == "SELECT_FIRST_SLOT" or (
                            turn.get("action") is None and "改" not in turn["user"]
                            and any(k in turn["user"] for k in ("第一个", "选第一", "生成预览", "准备")))):
                        payload["selected_slot_id"] = candidates[0]["slot_id"]
                    if structured.get("preview_id"):
                        preview_context = structured
                    if turn.get("action", "").startswith("CONFIRM"):
                        if not preview_context.get("preview_id"):
                            raise AssertionError(f"{case['case_id']} 确认前响应未返回 preview_id：{previous}")
                        payload.update(preview_id=preview_context["preview_id"],
                            confirmation_id=preview_context["confirmation_id"],
                            idempotency_key=f"{session_id}:{preview_context['preview_id']}"[:64])
                    response = client.post("/api/agent/chat", json=payload, headers=headers)
                    response.raise_for_status()
                    body = response.json()
                    responses.append(body)
                    answers.append(body["message"])
                    trace_ids.append(body["trace_id"])
                    if turn.get("action") == "CONFIRM_PREVIEW_TWICE":
                        replay = client.post("/api/agent/chat", json=payload, headers=headers)
                        replay.raise_for_status()
                        body = replay.json()
                        responses.append(body)
                        answers.append(body["message"])
                        trace_ids.append(body["trace_id"])
                # 每个模式使用独立只读会话，避免 SQLite 长事务快照漏掉刚提交的 Trace。
                with SessionLocal() as db:
                    traces = list(db.scalars(select(AgentRun).where(AgentRun.thread_id == session_id)
                                              .order_by(AgentRun.create_time)))
                    tool_calls = [call for trace in traces for call in json.loads(trace.trace_detail or "{}").get("tool_trace", [])]
                    citations = list(dict.fromkeys(c for trace in traces for c in json.loads(trace.trace_detail or "{}").get("rag_citations", [])))
                    final = responses[-1]
                    business = BusinessStateScorer().score(db, case, user_id, final["structured_data"]["state"])
                    trajectory = TrajectoryScorer().score(tool_calls, case["expected_business_state"], citations)
                    semantic = SemanticConditionScorer().score("\n".join(answers), case["expected_business_state"], citations)
                    passed = business["passed"] and trajectory["passed"] and semantic["passed"]
                    result = {"passed": passed, "business": business, "trajectory": trajectory,
                              "semantic": semantic, "final_state": final["structured_data"]["state"],
                              "answers": answers, "trace_ids": trace_ids, "http_statuses": [200] * len(responses)}
                pair[mode] = result
                report["versions"][mode]["passed" if passed else "failed"] += 1
            pair["regression_status"] = ("FIXED" if not pair["baseline"]["passed"] and pair["candidate"]["passed"]
                                         else "REGRESSED" if pair["baseline"]["passed"] and not pair["candidate"]["passed"]
                                         else "UNCHANGED_PASS" if pair["candidate"]["passed"] else "UNCHANGED_FAIL")
            report["cases"].append(pair)
    baseline_pass = report["versions"]["baseline"]["passed"]
    candidate_pass = report["versions"]["candidate"]["passed"]
    report["summary"] = {"baseline_pass_rate": baseline_pass / len(cases),
        "candidate_pass_rate": candidate_pass / len(cases),
        "fixed": sum(c["regression_status"] == "FIXED" for c in report["cases"]),
        "regressed": sum(c["regression_status"] == "REGRESSED" for c in report["cases"]),
        "candidate_business_pass_rate": sum(c["candidate"]["business"]["passed"] for c in report["cases"]) / len(cases),
        "candidate_trajectory_pass_rate": sum(c["candidate"]["trajectory"]["passed"] for c in report["cases"]) / len(cases),
        "candidate_semantic_pass_rate": sum(c["candidate"]["semantic"]["passed"] for c in report["cases"]) / len(cases),
        "candidate_duplicate_writes": sum(max(0, c["candidate"]["business"]["reservation_rows"] - 1) for c in report["cases"])}
    return report


def markdown(report: dict) -> str:
    summary = report["summary"]
    lines = ["# Agent 多轮与故障注入配对回归报告", "",
        "执行方式：原项目 FastAPI TestClient HTTP；独立 SQLite；动态读取时段与确认参数。", "",
        "| 版本 | 通过 | 失败 | 通过率 |", "|---|---:|---:|---:|",
        f"| Baseline v1 | {report['versions']['baseline']['passed']} | {report['versions']['baseline']['failed']} | {summary['baseline_pass_rate'] * 100:.2f}% |",
        f"| Candidate v2 | {report['versions']['candidate']['passed']} | {report['versions']['candidate']['failed']} | {summary['candidate_pass_rate'] * 100:.2f}% |", "",
        "| 用例 | Baseline | Candidate | 配对结果 | 最终状态 |", "|---|---|---|---|---|"]
    for case in report["cases"]:
        lines.append(f"| {case['case_id']} | {'PASS' if case['baseline']['passed'] else 'FAIL'} | {'PASS' if case['candidate']['passed'] else 'FAIL'} | {case['regression_status']} | {case['candidate']['final_state']} |")
    lines.extend(["", "## Candidate 三层评分", "",
        f"- 数据库业务状态：{summary['candidate_business_pass_rate'] * 100:.2f}%（{report['case_count']} 例）",
        f"- 工具轨迹与故障恢复：{summary['candidate_trajectory_pass_rate'] * 100:.2f}%（{report['case_count']} 例）",
        f"- 语义条件与引用：{summary['candidate_semantic_pass_rate'] * 100:.2f}%（{report['case_count']} 例）",
        f"- 重复预约写入：{summary['candidate_duplicate_writes']} 条",
        f"- FIXED：{summary['fixed']}；REGRESSED：{summary['regressed']}。", ""])
    return "\n".join(lines)


def main() -> None:
    REPORT_DIR.mkdir(exist_ok=True)
    report = run_agent_evaluation()
    (REPORT_DIR / "agent_eval_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    text = markdown(report)
    (REPORT_DIR / "agent_eval_report.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
