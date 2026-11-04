# Agent 多轮与故障注入配对回归报告

执行方式：原项目 FastAPI TestClient HTTP；独立 SQLite；动态读取时段与确认参数。

| 版本 | 通过 | 失败 | 通过率 |
|---|---:|---:|---:|
| Baseline v1 | 3 | 3 | 50.00% |
| Candidate v2 | 6 | 0 | 100.00% |

| 用例 | Baseline | Candidate | 配对结果 | 最终状态 |
|---|---|---|---|---|
| agent_001_normal_multi_turn_and_interrupt | PASS | PASS | UNCHANGED_PASS | SUCCEEDED |
| agent_002_modify_invalidates_preview | FAIL | PASS | FIXED | SUCCEEDED |
| agent_003_unqualified_user_blocked | FAIL | PASS | FIXED | FAILED |
| agent_004_fault_slot_taken_before_confirm | PASS | PASS | UNCHANGED_PASS | FAILED |
| agent_005_fault_timeout_after_write_idempotency | FAIL | PASS | FIXED | SUCCEEDED |
| agent_006_fault_duplicate_confirm | PASS | PASS | UNCHANGED_PASS | SUCCEEDED |

## Candidate 三层评分

- 数据库业务状态：100.00%（6 例）
- 工具轨迹与故障恢复：100.00%（6 例）
- 语义条件与引用：100.00%（6 例）
- 重复预约写入：0 条
- FIXED：3；REGRESSED：0。
