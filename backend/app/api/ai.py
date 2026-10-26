import json
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_user, get_token_issued_at
from app.models.user import User
from app.schemas.ai import ChatMessage, ChatRequest
from app.services import agent_service, quota_service

router = APIRouter(prefix="/ai", tags=["AI相关的API"])


def _guard(data: ChatRequest, db: Session, user: User, since: datetime) -> None:
    """对话前的两道闸：单条长度 → 会话额度（+ 日兜底）。

    放在路由里（而不是 stream_agent 里）是有意的：
    额度不足要能返回一条正常的业务错误，而不是先建立 SSE 流再吐个 error 事件——
    前者前端直接弹提示，后者要在流里做特殊处理。
    """
    last = data.messages[-1].content if data.messages else ""
    quota_service.check_input_length(last)
    quota_service.check(db, user, since)


@router.get("/quota")
def quota(
    current_user: User = Depends(get_current_user),
    since: datetime = Depends(get_token_issued_at),
    db: Session = Depends(get_db),
):
    """本次登录的 AI 用量与剩余额度（前端用来显示提示）。

    额度按登录会话计：退出重新登录 → 新 token → 额度刷新。
    """
    return Response.success(data=quota_service.get_usage(db, current_user, since))


@router.post("/chat")
async def chat(
    data: ChatRequest,
    current_user: User = Depends(get_current_user),
    since: datetime = Depends(get_token_issued_at),
    db: Session = Depends(get_db),
):
    # 非流式接口：一次性返回完整回复
    _guard(data, db, current_user, since)
    content = await agent_service.run_agent(db, current_user, data)
    return Response.success(data=ChatMessage(role="assistant", content=content))


def _sse_line(payload: dict) -> str:
    """拼一条 SSE 帧：必须以 data: 开头，且以空行（\n\n）结束。"""
    # ensure_ascii=False：中文不要被转成 \uXXXX
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(
    data: ChatRequest,
    current_user: User = Depends(get_current_user),
    since: datetime = Depends(get_token_issued_at),
    db: Session = Depends(get_db),
):
    _guard(data, db, current_user, since)

    async def event_gen():
        # 内层生成器：取出业务事件，包装成 SSE 文本再往外 yield
        async for event in agent_service.stream_agent(db, current_user, data):
            yield _sse_line(event)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",  # 告诉浏览器这是 SSE 流
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 有 Nginx 时避免缓冲整段再吐
        },
    )
