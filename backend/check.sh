#!/usr/bin/env bash
# 改完代码跑这个。30 秒出结果，不花 token。
#
# 用法：
#   cd E:/AI_Study/Course/lab-agent/backend
#   bash check.sh
#
# 检查三件事：
#   1. 语法          —— compileall，零依赖
#   2. import 冒烟   —— 应用能装配起来吗（能抓出循环导入、缺依赖）
#   3. 离线评测      —— lab-ai 的 30 条黄金集，跑假数据，零成本
#
# 不做什么（要手动跑）：
#   - 端到端 7 条：要 MySQL + 真模型，花钱
#     cd backend && .venv/Scripts/python.exe "E:/AI_Study/Course/ai对话/lab-ai/scripts/run_eval_lab_agent.py"
#
# 为什么没有 ruff：原项目没装，不想为了检查再动它的依赖。
# 要加的话：.venv/Scripts/python.exe -m pip install ruff

set -u

BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$BACKEND/.venv/Scripts/python.exe"
LAB_AI="E:/AI_Study/Course/ai对话/lab-ai"

fail=0
step() { echo ""; echo "== $1 =="; }

# ── 1. 语法 ───────────────────────────────────────────
step "1/3 语法检查"
if "$PY" -m compileall -q "$BACKEND/app" > /dev/null 2>&1; then
    echo "  OK"
else
    echo "  FAIL"
    "$PY" -m compileall "$BACKEND/app" 2>&1 | tail -5
    fail=1
fi

# ── 2. import 冒烟 ────────────────────────────────────
step "2/3 import 冒烟"
if out=$(cd "$BACKEND" && "$PY" -c "
import sys; sys.path.insert(0, '.')
from app.main import app
from app.services import agent_service
print(f'路由 {len(app.routes)} 个，TOOL_LABELS {len(agent_service.TOOL_LABELS)} 个')
" 2>&1); then
    echo "  OK  $out"
else
    echo "  FAIL"
    echo "$out" | tail -8
    fail=1
fi

# ── 3. 离线评测 ───────────────────────────────────────
step "3/3 离线评测（30 条黄金集，假数据）"
if out=$(cd "$LAB_AI" && "$PY" scripts/run_eval.py 2>&1); then
    echo "$out" | grep -E "^总计|^token" | sed 's/^/  /'
else
    echo "  FAIL"
    echo "$out" | tail -8
    fail=1
fi

# ── 收口 ──────────────────────────────────────────────
echo ""
if [ "$fail" -eq 0 ]; then
    echo "全部通过。"
    echo "提醒：端到端 7 条没跑（要真库 + 真模型），改动涉及接口/工具时记得手动跑一次。"
else
    echo "有检查没过，别急着提交。"
fi
exit "$fail"
