from datetime import datetime

from pydantic import BaseModel, Field


class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    title: str | None = Field(default=None, min_length=2, max_length=120)
    comment: str | None = Field(default=None, min_length=2, max_length=2000)


class ReviewUpdate(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=5)
    title: str | None = Field(default=None, min_length=2, max_length=120)
    comment: str | None = Field(default=None, min_length=2, max_length=2000)


class ReviewRead(BaseModel):
    id: str
    property_id: str
    user_id: str
    rating: int
    title: str | None = None
    comment: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ReviewListResponse(BaseModel):
    items: list[ReviewRead]
    total: int
    average_rating: float | None = None
