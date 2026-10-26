from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.response import PageResponse, Response
from app.database import get_db
from app.dependencies.auth import get_current_admin
from app.models.user import User
from app.schemas.agent_run import AgentRunDetail, AgentRunItem
from app.services import agent_run_service

# 只给管理员：这里能看到所有人的提问内容和 token 花费
router = APIRouter(prefix="/agent-admin", tags=["Agent 运营后台"])


@router.get("/stats")
def get_stats(
    days: int = Query(7, ge=1, le=90, description="统计最近 N 天"),
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return Response.success(data=agent_run_service.get_stats(db, days))


@router.get("/runs")
def get_run_list(
    page: int = 1,
    page_size: int = 10,
    status: int | None = None,
    user_id: int | None = None,
    keyword: str | None = None,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    res = agent_run_service.get_run_page_list(
        db, page, page_size, status, user_id, keyword
    )
    return Response.success(
        data=PageResponse(
            list=[AgentRunItem.model_validate(r) for r in res["list"]],
            total=res["total"],
        )
    )


@router.get("/runs/{run_id}")
def get_run_detail(
    run_id: int,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    run = agent_run_service.get_run_detail(db, run_id)
    return Response.success(data=AgentRunDetail.model_validate(run))
