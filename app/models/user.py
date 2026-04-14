from datetime import datetime, timezone
from enum import Enum
from typing import Any

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRole(str, Enum):
    saas_admin = "saas_admin"
    restaurant_owner = "restaurant_owner"


class UserCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, description="Full name of the user")
    email: EmailStr = Field(..., description="Unique email address")
    password: str = Field(..., min_length=8, description="Plain-text password (min 8 characters)")
    role: UserRole = Field(default=UserRole.restaurant_owner, description="User role")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "Ahmed Al-Rashid",
            "email": "ahmed@restaurant.com",
            "password": "securepass123",
            "role": "restaurant_owner",
        }
    })


class UserInDB(BaseModel):
    id: str
    name: str
    email: EmailStr
    role: UserRole
    hashed_password: str
    created_at: datetime

    model_config = ConfigDict(arbitrary_types_allowed=True)


class UserResponse(BaseModel):
    id: str = Field(..., description="MongoDB ObjectId as string")
    name: str
    email: EmailStr
    role: UserRole
    created_at: datetime

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id": "664f1c2e8a1b2c3d4e5f6a7b",
            "name": "Ahmed Al-Rashid",
            "email": "ahmed@restaurant.com",
            "role": "restaurant_owner",
            "created_at": "2024-06-01T10:00:00Z",
        }
    })


def user_from_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert a MongoDB user document to a plain dict with id as string."""
    doc = doc.copy()
    doc["id"] = str(doc.pop("_id"))
    return doc
