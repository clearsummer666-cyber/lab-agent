from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AnnouncementResponse(BaseModel):
    id: int
    title: str
    content: str
    author_id: int
    author_name: str | None = None
    is_top: int
    status: int
    create_time: datetime

    model_config = ConfigDict(from_attributes=True)


class AnnouncementCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)
    is_top: int = 0
    status: int = 1


class AnnouncementUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    content: str | None = Field(default=None, min_length=1)
    is_top: int | None = None
    status: int | None = None
