from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.core.roles import UserRole


class UserBase(BaseModel):
    email: EmailStr
    full_name: str
    is_active: bool = True
    role: UserRole = UserRole.GUEST


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str


class UserAdminUpdate(BaseModel):
    full_name: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None


class UserRead(UserBase):
    id: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class UserListResponse(BaseModel):
    items: list[UserRead]
    total: int
    skip: int
    limit: int
