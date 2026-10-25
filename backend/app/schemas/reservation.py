from pydantic import BaseModel, ConfigDict


class ReservationCreateRequest(BaseModel):
    lab_id: int
    equipment_id: int | None = None
    date: str
    start_time: str
    end_time: str
    # 预约人数。可选——前端目前不传，所以留 None 保持向后兼容。
    # 加它是因为 AI 对话层会收集人数并做容量校验，但原来这个值传给
    # service 时被 Pydantic 丢掉了（schema 里没这个字段），校验形同虚设。
    # 注意：reservations 表里**没有** people 列，这个字段只用于校验，不落库。
    people: int | None = None
    remark: str | None = None


class ReservationResponse(BaseModel):
    id: int
    user_id: int
    user_name: str | None = None
    lab_id: int
    lab_name: str | None = None
    equipment_id: int | None = None
    equipment_name: str | None = None
    type: str | None = None
    date: str
    start_time: str
    end_time: str
    remark: str | None = None
    status: int

    model_config = ConfigDict(from_attributes=True)


class AuditReservationRequest(BaseModel):
    status: int  #  1 | 2
