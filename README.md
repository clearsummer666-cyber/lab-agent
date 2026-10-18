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
- `recursion_limit=10`：限制 agent ⇄ tools 往返次数，防止模型空转死循环

### 1.2 工具清单

| 工具 | 类型 | 说明 |
|---|---|---|
| `search_lab_docs` | 读 | 检索实验室规则 / 安全规范 / 开放时间知识库 |
| `list_open_labs` | 读 | 按关键词查询开放中的实验室 |
| `list_lab_equipments` | 读 | 查询指定实验室的设备清单与状态 |
| `list_my_reservations` | 读 | 查询当前用户的预约记录（可按状态过滤） |
| `get_today` | 读 | 获取服务器当前日期，避免模型臆测「明天」 |
| `create_lab_reservation` | **写** | 创建预约单并落库 |
| `cancel_my_reservation` | **写** | 取消本人预约单 |

工具通过闭包注入 `db` 会话与 `current_user`，**复用业务 Service 层的校验逻辑**（开放时段、时间区间重叠、设备维修状态、越权校验）。Agent 无法绕过既有业务规则，也无法跨用户操作数据。

### 1.3 写操作安全边界

- **确认前置**：创建 / 取消前必须向用户复述实验室、设备、日期与时段，收到明确确认后才执行
- **参数不得臆造**：缺少 `lab_id` / `equipment_id` 时必须先查询，禁止凭空生成
- **日期不得猜测**：涉及「今天 / 明天 / 后天」必须先调用 `get_today`
- **失败不中断**：工具内部异常统一转为结构化 JSON（`{"ok": false, "error": ...}`）返回给模型，由模型向用户解释或重试，而非抛 500 打断整轮对话

### 1.4 RAG 检索

- **向量库**：ChromaDB 持久化到 `backend/data/chroma/`
- **Embedding**：`BAAI/bge-small-zh-v1.5` 本地推理，无需外部 Embedding API
- **切分与建库**：按 Markdown 文档整篇入库，元数据保留来源文件名；Collection 为空时自动建库
- **召回策略**：`score = 1 / (1 + distance)` 转相似度，低于 `0.5` 丢弃，取 Top-2 拼装上下文，减少无关内容污染
- **性能**：Embedding 模型与 Collection 均为进程内懒加载单例，服务启动时 `warmup()` 预热，把模型加载开销前移到启动阶段而非首个用户请求

### 1.5 流式输出协议

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
| LLM 编排 | LangGraph + LangChain |
| RAG | ChromaDB + sentence-transformers (BAAI/bge-small-zh-v1.5) |
| 配置 | pydantic-settings |

**前端** `frontend/`

| 类别 | 选型 |
|---|---|
| 框架 | Vue 3 (Composition API) |
| 构建 | Vite |
| UI | Element Plus |
| 路由 | Vue Router |
| 请求 | Axios |
| Markdown 渲染 | marked + DOMPurify |

**部署**：Docker Compose（MySQL / FastAPI / Nginx）+ Let's Encrypt

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

# 安装依赖
pip install -r requirements.txt

# 配置环境变量：复制 .env.example 为 .env 并填写真实值
cp .env.example .env

# 启动服务（首次启动会加载 embedding 模型并建库，耗时较长属正常）
python -m uvicorn app.main:app --port 8000 --reload --reload-dir app
```

> `python-multipart` 为文件上传功能的必要依赖，已在 `requirements.txt` 中声明。
> 离线环境可设 `HF_HUB_OFFLINE=1`，模型需提前下载至 HuggingFace 缓存。

### 4.2 前端

```bash
cd frontend
npm install
npm run dev
```

默认访问 `http://localhost:5173`。

### 4.3 Docker 部署

```bash
cp deploy.env.example deploy.env   # 逐项填写真实值
docker compose --env-file deploy.env up -d --build
```

Nginx 同源反代静态前端与 `/api`、`/uploads`，不产生跨域；针对 SSE 关闭 `proxy_buffering`，避免整段缓冲后才下发。

---

## 五、目录结构

```
lab-agent/
├── backend/
│   ├── app/
│   │   ├── api/          # 路由层（auth/lab/equipment/reservation/user/files/ai）
│   │   ├── models/       # SQLAlchemy 模型
│   │   ├── schemas/      # Pydantic 数据校验
│   │   ├── services/     # 业务服务 + agent_service / agent_tools / kb_service
│   │   ├── common/       # 统一响应与全局异常
│   │   ├── utils/        # 密码哈希、JWT
│   │   ├── config.py
│   │   ├── database.py
│   │   └── main.py
│   ├── data/kb/          # RAG 知识库原文（Markdown）
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── api/          # 接口封装（含 SSE 解析）
│   │   ├── components/
│   │   ├── layouts/
│   │   ├── router/
│   │   ├── views/        # 页面（含 AIChat.vue）
│   │   ├── assets/
│   │   ├── App.vue
│   │   └── main.js
│   ├── package.json
│   └── vite.config.js
├── docker-compose.yml
├── deploy.env.example
└── .gitignore
```

---

## 六、环境变量

后端配置项见 `backend/.env.example`，部署配置见 `deploy.env.example`。

> `deploy.env` 与 `backend/.env` 已被 `.gitignore` 忽略，**请勿提交真实密钥**。

---

## License

MIT
