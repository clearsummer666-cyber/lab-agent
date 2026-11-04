# RAG 四组消融实测报告

用例数：6；RRF：k=60；Top-k：3。
检索实现：本地特征哈希向量、jieba + rank_bm25，无外部模型调用。
Hit/MRR/Top-1/过期率/条件覆盖率以 6 例为分母；条款/规则码覆盖率以 5 个有标准答案用例为分母。

| 指标 | A 固定块+向量 | B 条款+混合RRF | C +元数据过滤 | D +重排+父块 |
|---|---:|---:|---:|---:|
| Hit Rate@3 | 83.33% | 83.33% | 83.33% | 83.33% |
| MRR | 0.8333 | 0.6667 | 0.8333 | 0.8333 |
| Top-1 命中率 | 66.67% | 50.00% | 83.33% | 83.33% |
| 过期规则误召回率 ↓ | 33.33% | 33.33% | 0.00% | 0.00% |
| 关键条件覆盖率 | 88.89% | 88.89% | 100.00% | 100.00% |
| 期望条款召回率@3（辅助） | 83.33% | 83.33% | 93.33% | 93.33% |
| 静态规则码覆盖率（辅助） | 0.00% | 90.00% | 100.00% | 100.00% |

## 逐例命中与覆盖率

| 策略 | 用例 | 返回 chunk_id | 条件覆盖率 |
|---|---|---|---:|
| A | rag_001_version_conflict | chem_rules_2026_01_art_03, chem_rules_2025_expired_art_03, chem_rules_2026_01_art_01 | 100.00% |
| A | rag_002_role_isolation | chem_rules_2026_01_art_01, chem_rules_2025_expired_art_03, chem_rules_2026_01_art_03 | 100.00% |
| A | rag_003_general_and_special_merge | chem_rules_2026_01_art_04, general_rules_2026_01_art_01, chem_rules_2025_expired_art_03 | 100.00% |
| A | rag_004_multihop_pcr | bio_pcr_rules_2026_01_art_02, bio_pcr_rules_2026_01_art_01, chem_rules_2026_01_art_04 | 100.00% |
| A | rag_005_cancellation_and_ban | general_rules_2026_01_art_03, general_rules_2026_01_art_01, general_rules_2026_01_art_02 | 33.33% |
| A | rag_006_unanswerable_guardrail | chem_rules_2025_expired_art_03, chem_rules_2026_01_art_02, chem_rules_2026_01_art_01 | 100.00% |
| B | rag_001_version_conflict | chem_rules_2025_expired_art_03, chem_rules_2026_01_art_03, chem_rules_2026_01_art_01 | 100.00% |
| B | rag_002_role_isolation | chem_rules_2025_expired_art_03, chem_rules_2026_01_art_01, chem_rules_2026_01_art_03 | 100.00% |
| B | rag_003_general_and_special_merge | chem_rules_2026_01_art_04, general_rules_2026_01_art_01, chem_rules_2025_expired_art_03 | 100.00% |
| B | rag_004_multihop_pcr | bio_pcr_rules_2026_01_art_01, bio_pcr_rules_2026_01_art_02, general_rules_2026_01_art_02 | 100.00% |
| B | rag_005_cancellation_and_ban | general_rules_2026_01_art_03, general_rules_2026_01_art_01, general_rules_2026_01_art_02 | 33.33% |
| B | rag_006_unanswerable_guardrail | chem_rules_2025_expired_art_03, chem_rules_2026_01_art_03, chem_rules_2026_01_art_02 | 100.00% |
| C | rag_001_version_conflict | chem_rules_2026_01_art_03, general_rules_2026_01_art_01, chem_rules_2026_01_art_04 | 100.00% |
| C | rag_002_role_isolation | chem_rules_2026_01_art_01, general_rules_2026_01_art_01, general_rules_2026_01_art_03 | 100.00% |
| C | rag_003_general_and_special_merge | chem_rules_2026_01_art_04, general_rules_2026_01_art_01, chem_rules_2026_01_art_03 | 100.00% |
| C | rag_004_multihop_pcr | bio_pcr_rules_2026_01_art_01, bio_pcr_rules_2026_01_art_02, general_rules_2026_01_art_02 | 100.00% |
| C | rag_005_cancellation_and_ban | general_rules_2026_01_art_03, general_rules_2026_01_art_01, general_rules_2026_01_art_04 | 100.00% |
| C | rag_006_unanswerable_guardrail | chem_rules_2026_01_art_03, general_rules_2026_01_art_03, general_rules_2026_01_art_01 | 100.00% |
| D | rag_001_version_conflict | chem_rules_2026_01_art_03, chem_rules_2026_01_art_04, general_rules_2026_01_art_01 | 100.00% |
| D | rag_002_role_isolation | chem_rules_2026_01_art_01, general_rules_2026_01_art_01, general_rules_2026_01_art_03 | 100.00% |
| D | rag_003_general_and_special_merge | chem_rules_2026_01_art_04, chem_rules_2026_01_art_03, general_rules_2026_01_art_01 | 100.00% |
| D | rag_004_multihop_pcr | bio_pcr_rules_2026_01_art_01, bio_pcr_rules_2026_01_art_02, general_rules_2026_01_art_02 | 100.00% |
| D | rag_005_cancellation_and_ban | general_rules_2026_01_art_03, general_rules_2026_01_art_01, general_rules_2026_01_art_04 | 100.00% |
| D | rag_006_unanswerable_guardrail | chem_rules_2026_01_art_03, chem_rules_2026_01_art_04, general_rules_2026_01_art_03 | 100.00% |
