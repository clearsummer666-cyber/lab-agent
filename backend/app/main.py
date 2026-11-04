import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from app.database import Base, engine, ensure_schema_columns
from app.models.user import User
from app.models.lab import Lab
from app.models.equipment import Equipment
from app.models.reservation import Reservation
from app.models.announcement import Announcement
from app.models.agent_run import AgentRun
from app.models.rule_chunk import RuleChunk
from app.models.agent_session import AgentSession
from app.models.reservation_preview import ReservationPreview
from app.api import api
from app.config import UPLOAD_DIR
from fastapi.exceptions import RequestValidationError, HTTPException
from starlette.middleware.cors import CORSMiddleware
from app.common.exceptions import (
    BusinessException,
    business_exception_handler,
    global_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.services import kb_service, reservation_service

logger = logging.getLogger(__name__)

# 开发期兜底：表不存在时自动建，clone 下来直接能跑。
# 表结构**变更**必须走 Alembic（backend/alembic/）—— create_all 只建不改，
# 对已有库加字段/改索引完全不生效：
#   alembic revision --autogenerate -m "说明"  &&  alembic upgrade head
Base.metadata.create_all(bind=engine)
ensure_schema_columns(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 预热向量库：把 embedding 模型的加载放到启动阶段，
    # 避免首个请求因现场加载模型而超过前端超时时间。
    try:
        await asyncio.to_thread(kb_service.warmup)  # to_thread：避免阻塞事件循环
    except Exception:
        logger.exception("向量库预热失败，服务继续启动")

    # 启动项目开启异步的扫描任务
    task = asyncio.create_task(reservation_service.run_expire_scan())
    yield
    task.cancel()  # 关闭项目同时取消异步任务


# 兼容旧前端 /ai/* 与评测 /api/*，受控路由挂在原项目 /api 前缀下。
app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def controlled_test_context(request: Request, call_next):
    from app.services.controlled_tools import fault_inject

    # 故障注入仅用于评测；测试身份由 auth 依赖在同一 EVAL_MODE 下解析。
    token = fault_inject.set(request.headers.get("X-Fault-Inject")
                             if os.environ.get("EVAL_MODE") else None)
    try:
        return await call_next(request)
    finally:
        fault_inject.reset(token)


origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # 允许的前端源，不要直接写 ["*"]
    allow_credentials=True,  # ✅ 关键：允许前端携带 Authorization token
    allow_methods=["*"],  # 允许所有请求方法 GET POST PUT DELETE OPTIONS
    allow_headers=["*"],  # 允许所有请求头（包含Authorization）
)

app.include_router(api)

# 挂载静态资源：/uploads/xxx.jpg → uploads/xxx.jpg
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# 注册异常处理器
app.add_exception_handler(BusinessException, business_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
# 全局的异常兜底，必须放在最后注册！！
app.add_exception_handler(Exception, global_exception_handler)


@app.get("/")
def read_root():
    return {"message": "backend 起来了"}


@app.get("/health")
def health():
    return {"status": "ok"}
