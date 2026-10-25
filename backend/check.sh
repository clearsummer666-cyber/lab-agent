#!/usr/bin/env bash
# 改完代码跑这个。1 分钟出结果，不花 token。
#
# 用法：
#   cd E:/AI_Study/Course/lab-agent/backend
#   bash check.sh
#
# 检查四件事：
#   1. 语法          —— compileall，零依赖
#   2. 单元测试      —— pytest，SQLite 临时库，不需要 MySQL
#   3. import 冒烟   —— 应用能装配起来吗（能抓出循环导入、缺依赖）
#   4. 离线评测      —— tests/ 下的 30 条黄金集，跑假数据，零成本
#
# 不做什么（要手动跑）：
#   - 端到端评测：要 MySQL + 真模型，花钱，脚本不随仓库提供

set -u

BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$BACKEND/.venv/Scripts/python.exe"
TESTS="$BACKEND/tests"

fail=0
step() { echo ""; echo "== $1 =="; }

# ── 1. 语法 ───────────────────────────────────────────
step "1/4 语法检查"
if "$PY" -m compileall -q "$BACKEND/app" > /dev/null 2>&1; then
    echo "  OK"
else
    echo "  FAIL"
    "$PY" -m compileall "$BACKEND/app" 2>&1 | tail -5
    fail=1
fi

# ── 2. 单元测试 ───────────────────────────────────────
step "2/4 单元测试（pytest，SQLite 临时库）"
if ! "$PY" -m pytest --version > /dev/null 2>&1; then
    echo "  SKIP  未安装 pytest：pip install -r tests/requirements-dev.txt"
elif out=$(cd "$TESTS" && "$PY" -m pytest 2>&1); then
    echo "$out" | tail -1 | sed 's/^/  /'
else
    echo "  FAIL"
    echo "$out" | tail -20
    fail=1
fi

# ── 3. import 冒烟 ────────────────────────────────────
step "3/4 import 冒烟"
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

# ── 4. 离线评测 ───────────────────────────────────────
step "4/4 离线评测（30 条黄金集，假数据）"
if [ ! -d "$TESTS" ]; then
    echo "  SKIP  $TESTS 不存在"
elif out=$(cd "$TESTS" && "$PY" run_eval.py 2>&1); then
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
    echo "提醒：端到端评测没跑（要真库 + 真模型），改动涉及接口/工具时记得手动验一次。"
else
    echo "有检查没过，别急着提交。"
fi
exit "$fail"
