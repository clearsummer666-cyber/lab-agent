# FINAL_RESUME_METRICS · 实验室 RAG 与受控 Agent 实测数据

生成时间：2026-11-04T17:14:44+08:00（Asia/Shanghai）

## 实测环境与统计口径

- Python 3.14.3；SQLAlchemy + SQLite；FastAPI TestClient HTTP；jieba + rank_bm25。
- 向量采用本地 8192 维字符/词特征哈希及余弦相似度；RRF 常数 k=60；每组返回 Top-3。未调用外部大模型，结果可离线复现。
- RAG：6 条黄金用例；条款召回率与规则码覆盖率仅在 5 条有标准答案的用例上计算。A 的固定块通过 `article__fixed_NN` 溯源到条款后评分。
- Agent：6 条多轮/故障用例 × Baseline/Candidate 配对运行；每次重置同一数据库夹具；时段与确认参数从上一轮响应动态读取。
- MRR 为倒数排名均值，不写成百分数；基线关闭资质拦截、预览失效和写后超时恢复，其余业务约束保持一致。

## RAG 四组消融实测

| 指标 | A 固定块+向量 | B 条款+混合RRF | C +元数据过滤 | D +重排+父块 |
|---|---:|---:|---:|---:|
| Hit Rate@3 | 83.33% | 83.33% | 83.33% | 83.33% |
| MRR | 0.8333 | 0.6667 | 0.8333 | 0.8333 |
| Top-1 命中率 | 66.67% | 50.00% | 83.33% | 83.33% |
| 过期规则误召回率 ↓ | 33.33% | 33.33% | 0.00% | 0.00% |
| 关键条件覆盖率 | 88.89% | 88.89% | 100.00% | 100.00% |
| 期望条款召回率@3（辅助） | 83.33% | 83.33% | 93.33% | 93.33% |
| 静态规则码覆盖率（辅助） | 0.00% | 90.00% | 100.00% | 100.00% |

## Agent 配对回归实测

| 版本 | 通过数 | 通过率 |
|---|---:|---:|
| Baseline v1 | 3/6 | 50.00% |
| Candidate v2 | 6/6 | 100.00% |

| 用例 | Baseline | Candidate | 结果 |
|---|---|---|---|
| agent_001_normal_multi_turn_and_interrupt | PASS | PASS | UNCHANGED_PASS |
| agent_002_modify_invalidates_preview | FAIL | PASS | FIXED |
| agent_003_unqualified_user_blocked | FAIL | PASS | FIXED |
| agent_004_fault_slot_taken_before_confirm | PASS | PASS | UNCHANGED_PASS |
| agent_005_fault_timeout_after_write_idempotency | FAIL | PASS | FIXED |
| agent_006_fault_duplicate_confirm | PASS | PASS | UNCHANGED_PASS |

- FIXED：**3**；REGRESSED：**0**。
- Candidate 数据库状态 / 工具轨迹 / 语义条件三层通过率：**100.00% / 100.00% / 100.00%**。
- 重复确认及超时恢复场景的重复写入：**0 条**。

## 5 项设计漏洞对应实现与验证

| 修复项 | 实现 | 实测证据 |
|---|---|---|
| 通用规则漏召回 | `lab_type IN (target, general)`，按有效期、角色联合过滤 | 通风橱与 4 小时通用约束同答；C/D 静态规则码覆盖率 100% |
| rule_code 映射断层 | 入库静态 JSON 元数据，由 Python 集合组装 | C/D 规则码覆盖率 100%；A 为 0%，符合元数据剥离设定 |
| 正常预约冗余 RAG | 主链路直接查时段/预览；仅缺资质或规则问答触发检索 | `test_normal_flow_has_no_redundant_rule_search` 断言工具序列 |
| 动态故障卡点 | `X-Fault-Inject` + ContextVar；抢占、写后超时、空检索 | 抢占冲突反馈；超时后 `get_reservation` 核对并进入 SUCCEEDED |
| 多轮参数死板 | Runner 读取候选时段、preview_id、confirmation_id；修改日不携带旧选择 | 修改用例最终落库日期为 2026-11-07，旧预览为 INVALIDATED |

## 可复现命令与原始产物

```powershell
python -m pip install -r requirements.txt
python -m eval_suite.rag_ablation_runner
python -m eval_suite.agent_eval_runner
python -m eval_suite.run_all_evals
python -m pytest tests/test_lab_eval.py -q
```

逐例分数、完整回答、工具轨迹与 HTTP 结果见同目录 `rag_ablation_report.json` 与 `agent_eval_report.json`。
本报告为小规模黄金集功能/消融实测，不外推为通用模型能力或生产环境并发性能结论。
