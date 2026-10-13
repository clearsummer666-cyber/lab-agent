# 智能实验室预约系统 (lab-agent)

一个前后端分离的智能实验室预约系统，支持实验室/设备管理、在线预约审核，并集成 **RAG 知识库** 与 **LangGraph 智能预约 Agent**（含 Tool Calling 与 SSE 流式输出）。

## 技术栈

**后端** `backend/`

| 类别 | 选型 |
|---|---|
| Web 框架 | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.x + PyMySQL |
| 数据库 | MySQL 8.0 |
| 认证 | JWT (PyJWT) + bcrypt |
| LLM 编排 | LangChain + LangGraph |
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

## 功能模块

- **用户**：注册、登录、JWT 鉴权、个人信息修改、密码修改、用户增删改查（管理员）
- **实验室**：实验室管理与列表（管理员/学生端）
- **设备**：设备管理与列表
- **预约**：发起预约、我的预约、预约审核、过期自动扫描失效
- **文件**：通用文件上传接口
- **AI 助手**：基于 RAG 的实验室知识问答，Tool Calling 查询实验室数据，LangGraph 智能预约 Agent，SSE 流式输出

## 快速开始

### 1. 后端

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

# 初始化数据库（创建 lab_agent 库后启动会自动建表）
# 启动服务
python -m uvicorn app.main:app --port 8000 --reload --reload-dir app
```

> 说明：`python-multipart` 为文件上传功能的必要依赖，已在 `requirements.txt` 中声明。
> 首次启动会加载 embedding 向量模型，耗时较长属正常现象。

### 2. 前端

```bash
cd frontend
npm install
npm run dev
```

默认访问 `http://localhost:5173`。

## 目录结构

```
lab-agent/
├── backend/
│   ├── app/
│   │   ├── api/          # 路由层（auth/lab/equipment/reservation/user/files/ai）
│   │   ├── models/       # SQLAlchemy 模型
│   │   ├── schemas/      # Pydantic 数据校验
│   │   ├── services/     # 业务服务（含 RAG kb_service、Agent）
│   │   ├── common/       # 统一响应与全局异常
│   │   ├── utils/        # 密码哈希等工具
│   │   ├── config.py     # 配置加载
│   │   ├── database.py   # 数据库会话
│   │   └── main.py       # 应用入口
│   ├── data/kb/          # RAG 知识库原文（Markdown）
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── api/          # 接口封装
│   │   ├── components/   # 公共组件
│   │   ├── layouts/      # 布局
│   │   ├── router/       # 路由配置
│   │   ├── views/        # 页面
│   │   ├── assets/       # 静态资源
│   │   ├── App.vue
│   │   └── main.js
│   ├── package.json
│   └── vite.config.js
└── .gitignore
```

## 环境变量

后端配置项见 `backend/.env.example`，**请勿将真实 `.env` 提交到仓库**。

## License

MIT
