from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OrderItemRequest(BaseModel):
    item_id: str = Field(..., description="MenuItem ID")
    quantity: int = Field(..., ge=1, le=100, description="Number of units ordered")
    variant_name: str | None = Field(default=None, description="Selected variant name (required if item has variants)")

    model_config = ConfigDict(json_schema_extra={
        "example": {"item_id": "664f1c2e8a1b2c3d4e5f0020", "quantity": 2, "variant_name": "وجبة كاملة"}
    })


class OrderCreate(BaseModel):
    customer_name: str = Field(..., min_length=2, max_length=100, description="Customer full name")
    customer_phone: str = Field(..., min_length=7, max_length=20, description="Customer phone number")
    items: list[OrderItemRequest] = Field(..., min_length=1, description="At least one item required")
    notes: str | None = Field(default=None, max_length=500, description="Special instructions or allergy notes")
    nearest_location: str | None = Field(default=None, max_length=300, description="Nearest landmark or location description provided by the customer")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "customer_name": "Sara Ahmed",
            "customer_phone": "+966501234567",
            "items": [
                {"item_id": "664f1c2e8a1b2c3d4e5f0020", "quantity": 2},
                {"item_id": "664f1c2e8a1b2c3d4e5f0021", "quantity": 1},
            ],
            "notes": "No onions please",
        }
    })


class OrderItem(BaseModel):
    item_id: str
    name: str
    price: Decimal
    quantity: int
    subtotal: Decimal
    variant_name: str | None = None


class OrderResponse(BaseModel):
    id: str
    restaurant_id: str
    customer_name: str
    customer_phone: str
    items: list[OrderItem]
    notes: str | None = None
    nearest_location: str | None = None
    total: Decimal
    whatsapp_link: str
    created_at: datetime


def order_from_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert a MongoDB order document to a plain dict with id as string."""
    doc = doc.copy()
    doc["id"] = str(doc.pop("_id"))
    return doc
