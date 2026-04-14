from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReviewCreate(BaseModel):
    customer_name: str = Field(..., min_length=2, max_length=100, description="Reviewer's name")
    rating: int = Field(..., ge=1, le=5, description="Star rating from 1 to 5")
    comment: str | None = Field(default=None, max_length=500, description="Optional text comment")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "customer_name": "أحمد محمد",
            "rating": 5,
            "comment": "طعام رائع وخدمة ممتازة!",
        }
    })


class ReviewResponse(BaseModel):
    id: str
    restaurant_id: str
    customer_name: str
    rating: int
    comment: str | None = None
    created_at: datetime

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id": "664f1c2e8a1b2c3d4e5f0030",
            "restaurant_id": "664f1c2e8a1b2c3d4e5f6a7b",
            "customer_name": "أحمد محمد",
            "rating": 5,
            "comment": "طعام رائع!",
            "created_at": "2024-06-01T10:00:00Z",
        }
    })


class ReviewsListResponse(BaseModel):
    reviews: list[ReviewResponse]
    total: int
    average_rating: float


def review_from_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert a MongoDB review document to a plain dict with id as string."""
    doc = doc.copy()
    doc["id"] = str(doc.pop("_id"))
    return doc
