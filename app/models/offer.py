from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OfferType(str, Enum):
    percentage = "percentage"
    fixed_amount = "fixed_amount"


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class OfferCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=150, description="Offer display title")
    description: str | None = Field(default=None, max_length=500)
    discount_type: OfferType = Field(..., description="percentage or fixed_amount")
    discount_value: Decimal = Field(..., gt=0, decimal_places=2, description="Discount amount (% or currency unit)")
    applicable_items: list[str] = Field(
        default_factory=list,
        description="MenuItem IDs this offer applies to. Empty list means store-wide.",
    )
    start_date: datetime = Field(..., description="When the offer becomes active")
    end_date: datetime = Field(..., description="When the offer expires")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "title": "Welcome Deal — 10% Off",
            "description": "10% off your entire order, limited time only.",
            "discount_type": "percentage",
            "discount_value": "10.00",
            "applicable_items": [],
            "start_date": "2024-06-01T00:00:00Z",
            "end_date": "2024-06-30T23:59:59Z",
        }
    })

    @model_validator(mode="after")
    def validate_dates_and_value(self) -> "OfferCreate":
        if self.end_date <= self.start_date:
            raise ValueError("تاريخ الانتهاء يجب أن يكون بعد تاريخ البداية")
        if self.discount_type == OfferType.percentage and self.discount_value > 100:
            raise ValueError("نسبة الخصم لا يمكن أن تتجاوز 100%")
        return self


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

class OfferUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=500)
    discount_type: OfferType | None = None
    discount_value: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    applicable_items: list[str] | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    is_active: bool | None = None

    model_config = ConfigDict(json_schema_extra={
        "example": {"title": "Summer Special — 15% Off", "discount_value": "15.00"}
    })


# ---------------------------------------------------------------------------
# Response / InDB
# ---------------------------------------------------------------------------

class OfferResponse(BaseModel):
    id: str
    title: str
    description: str | None = None
    discount_type: OfferType
    discount_value: Decimal
    applicable_items: list[str]
    start_date: datetime
    end_date: datetime
    restaurant_id: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id": "664f1c2e8a1b2c3d4e5f0030",
            "title": "Welcome Deal — 10% Off",
            "description": "10% off your entire order, limited time only.",
            "discount_type": "percentage",
            "discount_value": "10.00",
            "applicable_items": [],
            "start_date": "2024-06-01T00:00:00Z",
            "end_date": "2024-06-30T23:59:59Z",
            "restaurant_id": "664f1c2e8a1b2c3d4e5f6a7b",
            "is_active": True,
            "created_at": "2024-06-01T00:00:00Z",
        }
    })


class OfferInDB(OfferResponse):
    pass


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def offer_from_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert a MongoDB offer document to a plain dict with id as string."""
    doc = doc.copy()
    doc["id"] = str(doc.pop("_id"))
    return doc
