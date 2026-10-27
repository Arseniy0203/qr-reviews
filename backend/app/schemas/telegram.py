from datetime import datetime

from pydantic import BaseModel, Field


class LinkCodeOut(BaseModel):
    deep_link: str
    expires_at: datetime


class InternalLinkRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    chat_id: int
