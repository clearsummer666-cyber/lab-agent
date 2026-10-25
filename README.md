# LabPilot · 实验室智能体

> 一个能读懂实验室规则、并代替用户完成预约操作的对话式 Agent。

用户用自然语言提问或下单，Agent 自主判断该**检索知识库**、**查询业务数据**还是**写入数据库**，全过程流式返回。业务侧以实验室资源预约为载体，完整实现 **理解 → 检索 → 调工具 → 落库 → 反馈** 的闭环。

区别于「给表单套一个聊天框」：路径由模型决策，因此工程重点落在**如何让模型不乱调**——执行图可观测、写操作需确认、工具复用既有业务校验。

---

## 一、Agent 链路

### 1.1 执行图

基于 LangGraph `StateGraph` 构建 ReAct 式执行图：

```
START ──▶ agent ──[tools_condition]──▶ tools ──┐
            ▲                │                 │
            └────────────────┴─────────────────┘
                     (无 tool_call 时结束)
```

- `agent` 节点：调用 LLM，输出自然语言或 `tool_calls`
- `tools` 节点：`ToolNode` 并发执行被选中的工具
- `tools_condition`：有 tool_call 则路由到 `tools`，否则结束
- `recursion_limit=25`：限制 agent ⇄ tools 往返次数，防止模型空转死循环。
  取 25 而非更小的值，是因为「约设备」这类任务要串 4 个工具、正常路径 8–9 步，
  模型一次重试就会触顶（实测 15 时偶发 `GraphRecursionError`）

### 1.2 工具清单（9 个）

| 工具 | 类型 | 说明 |
|---|---|---|
| `search_lab_docs` | 读 | 检索实验室规则 / 安全规范 / 开放时间知识库 |
| `list_open_labs` | 读 | 按关键词查询开放中的实验室 |
| `list_lab_equipments` | 读 | 查询指定实验室的设备清单与状态 |
| `list_availability` | 读 | 查询某天各实验室的空闲时段（用户还没定时间时先给可选范围） |
| `list_my_reservations` | 读 | 查询当前用户的预约记录（可按状态过滤） |
| `get_today` | 读 | 获取服务器当前日期，避免模型臆测「明天」 |
| `create_lab_reservation` | **写** | 创建预约单并落库 |
| `cancel_my_reservation` | **写** | 取消本人预约单 |
| `save_slot` | 内部 | 记录已确认的槽位（多轮补槽用），不落库 |

工具通过闭包注入 `SqlRepo(db, current_user.id)`，**复用业务 Service 层的校验逻辑**（开放时段、时间区间重叠、容量上限、设备维修状态、越权校验）。Agent 无法绕过既有业务规则，也无法跨用户操作数据。

所有工具统一返回 JSON 字符串，出错返回 `{"ok": false, "error": "..."}` 而**不抛异常** —— 抛异常会打断 Agent 循环，模型拿不到错误信息就无法自行纠正。

### 1.3 写操作安全边界

- **确认前置**：创建 / 取消前必须向用户复述实验室、设备、日期与时段，收到明确确认后才执行。
  目前由 system prompt 约束，**不是 `interrupt()` 硬闸门**（原前端无确认弹窗，硬闸门会让 SSE 永久停在「正在思考…」）
- **参数不得臆造**：缺少 `lab_id` / `equipment_id` 时必须先查询，禁止凭空生成
- **日期不得猜测**：涉及「今天 / 明天 / 后天」必须先调用 `get_today`
- **失败不中断**：工具内部异常统一转为结构化 JSON 返回给模型，由模型向用户解释或重试，而非抛 500 打断整轮对话
- **双层幂等**：业务层唯一索引 `uk_reservation_exact` + `SELECT ... FOR UPDATE` 是真防线；
  Agent 层的工具重试直接复用同一路径，不额外缓存

### 1.4 Agent 模块分层（`backend/app/ai/`）

Agent 能力单独成包，11 个文件 / 约 1350 行，与 `api` / `services` 平级。这是整个项目里唯一「超出 CRUD 业务」的一层：

| 文件 | 职责 |
|---|---|
| `builder.py` | 装配 `StateGraph`；checkpointer 单例（`SqliteSaver` 同步 / `AsyncSqliteSaver` 异步，指向同一文件） |
| `session.py` | `thread_id = f"user:{id}"`，前端不传，服务端定 |
| `state.py` | `BookingState`（含 `slots` 的 merge reducer，防模型一轮并行调多次 `save_slot` 触发 `InvalidUpdateError`） |
| `tools.py` | 9 个工具，闭包注入 `SqlRepo` |
| `repo.py` | `SqlRepo`：真库访问，写操作一律走既有 `reservation_service` |
| `trim.py` | 上下文裁剪中间件：不可驱逐区（system + 任务目标）常驻，历史按预算裁剪 |
| `memory_utils.py` | 裁剪算法（字符/token 估算、pin 保留） |
| `router.py` | 按意图动态挂工具：问规则类问题只挂读工具，省约 33% token |
| `retention.py` | 记忆 TTL：独立 `threads.db` 记活跃时间，每 100 次对话扫一次过期 thread |
| `middleware.py` | `SlotsMiddleware`：把槽位状态同步进上下文 |
| `__init__.py` | 统一出口；并在此处设 `HF_HUB_OFFLINE=1`（必须早于 embedding 模型加载） |

**会话记忆**：`thread_id` 固定为 `user:{id}`，历史存在 `backend/data/checkpoints.sqlite`，
**刷新页面、重启进程都不丢**。代价是同一用户开两个标签页会共享上下文。

**上下文裁剪实测**：默认预算 6000，第 6 轮省约 14% token。
裁剪只能压历史部分（省 28%），因为第 1 轮就有约 1200 token 的固定开销（9 个工具的 schema）。

### 1.5 RAG 检索

- **向量库**：ChromaDB 持久化到 `backend/data/chroma/`
- **Embedding**：`BAAI/bge-small-zh-v1.5` 本地推理，无需外部 Embedding API
- **切分与建库**：按 Markdown 文档整篇入库，元数据保留来源文件名；Collection 为空时自动建库
- **召回策略**：`score = 1 / (1 + distance)` 转相似度，低于 `0.5` 丢弃，取 Top-2 拼装上下文，减少无关内容污染
- **性能**：Embedding 模型与 Collection 均为进程内懒加载单例，服务启动时 `warmup()` 预热，把模型加载开销前移到启动阶段而非首个用户请求

### 1.6 流式输出协议

后端 `astream_events(version="v2")` 从图执行过程中抽取事件，经 SSE 推送到前端：

| 事件 | 触发时机 | 前端表现 |
|---|---|---|
| `status` | 开始思考 | 显示「正在思考…」 |
| `tool_start` | 工具开始执行 | 显示「检索实验室知识库」「提交预约」等中文过程文案 |
| `tool_end` | 工具执行完成 | 展示结果预览（截断至 200 字符） |
| `token` | 模型逐字输出 | 逐字渲染，仅取 `agent` 节点，过滤内部节点噪声 |
| `done` | 正常结束 | 结束 loading |
| `error` | 业务或模型异常 | 流内报错，不降级为普通 JSON |

前端因 `EventSource` 无法携带 `Authorization` 头，改用 `fetch + ReadableStream` 手动解析 SSE 帧，处理 TCP 半包与 UTF-8 多字节截断；回答经 `marked` 渲染 Markdown 后由 `DOMPurify` 过滤，防止 XSS。

---

## 二、技术栈

**后端** `backend/`

| 类别 | 选型 |
|---|---|
| Web 框架 | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.x + PyMySQL |
| 数据库 | MySQL 8.0 |
| 认证 | JWT (PyJWT) + bcrypt |
| LLM 编排 | LangGraph + LangChain（`gpt-6-luna`，OpenAI 兼容协议） |
| 会话持久化 | `langgraph-checkpoint-sqlite`（SqliteSaver / AsyncSqliteSaver） |
| RAG | ChromaDB + sentence-transformers (BAAI/bge-small-zh-v1.5) |
| 配置 | pydantic-settings + python-dotenv |

**前端** `frontend/`

| 类别 | 选型 |
|---|---|
| 框架 | Vue 3 (Composition API) |
| 构建 | Vite |
| UI | Element Plus |
| 路由 | Vue Router |
| 请求 | Axios |
| Markdown 渲染 | marked + DOMPurify |

---

## 三、功能模块

- **Agent 对话**：知识库问答、数据查询、预约创建与取消，全程流式 + 工具执行过程可视化
- **用户**：注册、登录、JWT 鉴权、个人信息与密码修改、用户管理（管理员）
- **实验室**：实验室管理与列表（管理员 / 学生端）
- **设备**：设备管理与列表
- **预约**：发起预约、我的预约、预约审核、过期自动扫描失效
- **文件**：通用文件上传接口

---

## 四、快速开始

### 4.1 后端

```bash
cd backend

# 创建虚拟环境
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate

# 安装依赖（20 条，版本全部钉死且已实测）
pip install -r requirements.txt

# 配置环境变量（只需一个文件：数据库 / JWT / 模型配置全在里面）
cp .env.example .env

# 启动服务（首次启动会加载 embedding 模型并建库，耗时较长属正常）
python -m uvicorn app.main:app --port 8000 --reload --reload-dir app
```

**表结构变更走 Alembic**（`backend/alembic/`）：

```bash
cd backend
alembic upgrade head                          # 升到最新版本
alembic revision --autogenerate -m "加字段"   # 改完模型后生成迁移脚本
alembic downgrade -1                          # 回退一步
alembic current                               # 看当前版本
```

`alembic.ini` 里**故意不写连接串**，`env.py` 从 `settings.DATABASE_URL` 注入 —— 避免密码进 git。
`main.py` 里的 `create_all` 只是「表不存在时兜底」，**它只建不改**：给已有库加字段、改索引完全不生效，
所以变更必须走迁移。baseline 已 stamp，`upgrade head` 不会重复建表。


> **依赖里三个容易被漏掉的**（已写进 `requirements.txt`，列出来是为了讲清作用）：
> - `python-multipart` —— 文件上传接口（`/api/files/upload`）必需，缺了 uvicorn 直接起不来
> - `langgraph-checkpoint-sqlite` —— 会话持久化（`SqliteSaver` / `AsyncSqliteSaver`）
> - `aiosqlite` —— 异步 checkpointer 的连接驱动
>
> 离线环境可设 `HF_HUB_OFFLINE=1`，模型需提前下载至 HuggingFace 缓存
> （`BAAI/bge-small-zh-v1.5`，约 96MB）。`app/ai/__init__.py` 已默认设置该变量，
> 但首次下载时需临时取消。

**模型配置两套并存，都在 `backend/.env` 里**：`AI_LLM_*` 优先，没配齐就**整套**回落到 `LLM_*`。
这样业务兜底模型撞限流（实测 429）时，只换 `AI_LLM_*` 就能把对话切到别的模型，不用动业务配置。
**改完必须重启服务才生效** —— pydantic-settings 只在进程启动时读一次。

**改完代码跑一次自检**：

```bash
cd backend && bash check.sh
```

四步：`compileall` 语法检查 → `pytest` 单元测试（SQLite 临时库，不需要 MySQL）→ import 冒烟（路由数 + 工具数）→ 30 条离线黄金集评测。约 1 分钟，零 API 成本。

**单元测试**（`backend/tests/test_*.py`）覆盖的是业务真风险点，不是凑覆盖率：

| 用例组 | 覆盖点 |
|---|---|
| `test_reservation.py` | 区间重叠的 7 种形态、时间归一化、容量上限、开放时段、四态状态机非法流转、越权取消、唯一索引兜底 |
| `test_auth.py` | JWT 寿命与配置一致、过期/篡改 token、禁用用户、学生越权调管理员接口、bcrypt 加盐 |
| `test_tools.py` | 工具参数不臆造、业务异常降级为 `{"ok": false}`、跨用户隔离、输出格式 |

跑测试要先装开发依赖：

```bash
cd backend && pip install -r tests/requirements-dev.txt
cd tests && python -m pytest
```

> 测试库里两个 `xfail` 是**已知缺口的活文档**（service 层不校验设备归属、`equipment_id` 为 NULL 时唯一索引不生效）。
> 哪天补上了，它们会自动变成 `XPASS` 并报错，提醒你更新这条记录。

离线评测集在 `backend/tests/`，覆盖 10 个 suite（核心任务 / 知识问答 / 多轮指代 / 多轮改口 /
异常输入 / 工具调用 / 多意图 / 模糊意图 / 长对话衰减 / 幂等），跑的是脚本化假 Agent + 假数据，
判定靠 `expect` 断言 + `forbid` 护栏。也可以单独跑：

```bash
cd backend/tests && ../.venv/Scripts/python.exe run_eval.py --critical --k 5
```

端到端那两套（AI 对话 12 条 / 普通预约 13 条）要真 MySQL + 真模型，**脚本未随仓库提供**。

### 4.2 前端

```bash
cd frontend
npm install
npm run dev
```

默认访问 `http://localhost:5173`。

---

## 五、目录结构

```
lab-agent/
├── backend/
│   ├── app/
│   │   ├── ai/           # ★ Agent 能力包（11 文件，见 1.4 节）
│   │   ├── api/          # 路由层：auth / lab / equipment / reservation / user / files / ai
│   │   ├── services/     # 业务服务：auth / lab / equipment / reservation / user / kb / agent
│   │   ├── models/       # SQLAlchemy 模型
│   │   ├── schemas/      # Pydantic 数据校验
│   │   ├── dependencies/ # FastAPI 依赖：get_current_user 等
│   │   ├── common/       # 统一响应 + 全局异常处理
│   │   ├── utils/        # 密码哈希、JWT
│   │   ├── config.py     # pydantic-settings 配置
│   │   ├── database.py   # 引擎 / SessionLocal / get_db
│   │   └── main.py       # 应用入口、lifespan、CORS、路由挂载
│   ├── data/
│   │   ├── kb/           # RAG 知识库原文（4 篇 Markdown，入库）
│   │   ├── chroma/       # 向量库（运行期生成，已 gitignore）
│   │   ├── checkpoints.sqlite  # 会话记忆（运行期生成，已 gitignore）
│   │   └── threads.db    # 记忆 TTL 记录（运行期生成，已 gitignore）
│   ├── uploads/          # 上传文件（运行期生成，已 gitignore）
│   ├── tests/            # 全部测试代码与配置
│   │   ├── conftest.py   # pytest 夹具：临时 SQLite 库 + 用户/实验室工厂
│   │   ├── test_*.py     # 单元测试：预约 / 鉴权 / Agent 工具
│   │   ├── pytest.ini    # pytest 配置（rootdir 就是本目录）
│   │   ├── requirements-dev.txt  # 测试依赖（pytest）
│   │   ├── run_eval.py   # 评测入口（--critical / --k / --only）
│   │   ├── data.py       # 假数据层（4 实验室 / 5 设备）
│   │   └── eval/         # core / graders / harness / sut / tasks
│   ├── check.sh          # 自检脚本：语法 + 单元测试 + 冒烟 + 30 条离线评测
│   ├── requirements.txt
│   ├── alembic.ini       # 迁移配置（连接串由 env.py 注入）
│   ├── alembic/          # 表结构迁移：env.py + versions/
│   ├── .env.example      # 配置模板（数据库 / JWT / 模型）
│   └── .env              # 实际配置（本地，含 key，不提交）
├── frontend/
│   ├── src/
│   │   ├── api/          # 接口封装（含 SSE 手动解析）
│   │   ├── components/   # ReserveDialog 等复用组件
│   │   ├── layouts/      # 主框架布局（顶栏 + 侧栏）
│   │   ├── router/       # 路由 + 登录守卫
│   │   ├── utils/        # request 拦截器、auth、user
│   │   ├── views/        # 页面（含 AIChat.vue）
│   │   ├── assets/       # 全局样式
│   │   ├── App.vue
│   │   └── main.js
│   ├── package.json
│   └── vite.config.js
├── 资料pdf/              # 教程原文（本地，不提交）
└── .gitignore
```

---

## 六、环境变量

只需要一个文件：`backend/.env`（从 `backend/.env.example` 复制）。

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | MySQL 连接串 |
| `JWT_SECRET_KEY` / `JWT_EXPIRE_HOURS` / `JWT_ALGORITHM` | JWT 签发参数 |
| `AI_LLM_MODEL` / `AI_LLM_BASE_URL` / `AI_LLM_API_KEY` | AI 对话用的模型，**优先** |
| `LLM_MODEL` / `LLM_BASE_URL` / `LLM_API_KEY` | 兜底模型，`AI_LLM_*` 没配齐时整套回落 |

> **两套模型配置必须至少配齐一套**（各三个字段都要有）。`Settings` 里做了启动期校验，
> 缺了服务直接起不来，不会等到第一个用户提问才报错。
>
> 回落是**整套**的，不是逐字段 `or` —— 否则会出现 model 取 `AI_LLM_MODEL`、
> key 取 `LLM_API_KEY` 的混搭，连上去就是 401。
>
> 2026-10-25 合并：原来 AI 部分单独放 `backend/.env.ai`，靠 `load_dotenv` 塞进环境变量；
> 现在统一由 pydantic-settings 从 `.env` 读，少一个文件、少一个「clone 下来忘了 cp」的坑。
> `.env` 被 `.gitignore` 忽略，**请勿提交真实密钥**。

---

## License

MIT
