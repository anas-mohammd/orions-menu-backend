from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DeliveryInfo(BaseModel):
    available: bool = False
    message: str | None = Field(default=None, max_length=500, description="Free-text delivery info appended to WhatsApp message")


class SubscriptionStatus(str, Enum):
    active = "active"
    expired = "expired"
    suspended = "suspended"


class SubscriptionPlan(str, Enum):
    monthly = "monthly"
    yearly = "yearly"


class Restaurant(BaseModel):
    id: str = Field(..., description="MongoDB ObjectId as string")
    name: str
    slug: str = Field(..., description="URL-safe identifier used in public menu links")
    description: str | None = None
    logo_url: str | None = None
    whatsapp_number: str = Field(..., description="Restaurant WhatsApp number (E.164 without +)")
    instagram_url: str | None = Field(default=None, description="Instagram profile URL or username")
    phone_number: str | None = Field(default=None, description="Direct call phone number")
    currency_code: Literal["IQD", "USD"] = Field(default="IQD", description="رمز العملة: IQD (دينار عراقي) أو USD (دولار أمريكي)")
    owner_ids: list[str]
    subscription_start_date: datetime
    subscription_expires_at: datetime
    subscription_status: SubscriptionStatus
    subscription_plan: SubscriptionPlan = Field(default=SubscriptionPlan.monthly, description="Subscription plan type")
    subscription_price: float = Field(default=0.0, description="Subscription price in IQD")
    delivery_info: DeliveryInfo | None = Field(default=None, description="Delivery configuration")
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id": "664f1c2e8a1b2c3d4e5f6a7b",
            "name": "The Golden Fork",
            "slug": "the-golden-fork",
            "description": "Classic comfort food with a modern twist",
            "logo_url": None,
            "whatsapp_number": "966501234567",
            "owner_ids": ["664f1c2e8a1b2c3d4e5f0001"],
            "subscription_start_date": "2024-01-01T00:00:00Z",
            "subscription_expires_at": "2025-01-01T00:00:00Z",
            "subscription_status": "active",
            "is_active": True,
            "created_at": "2024-01-01T00:00:00Z",
        }
    })


class RestaurantCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, description="Restaurant display name")
    whatsapp_number: str = Field(..., min_length=7, max_length=20, description="WhatsApp number customers will message")
    owner_ids: list[str] = Field(..., min_length=1, description="IDs of restaurant_owner users who manage this restaurant")
    expires_at: datetime = Field(..., description="Subscription expiry date")
    subscription_plan: SubscriptionPlan = Field(default=SubscriptionPlan.monthly, description="Subscription plan type")
    subscription_price: float = Field(default=0.0, ge=0, description="Subscription price in IQD")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "The Golden Fork",
            "whatsapp_number": "966501234567",
            "owner_ids": ["664f1c2e8a1b2c3d4e5f0001"],
            "expires_at": "2025-12-31T23:59:59Z",
        }
    })


class RestaurantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    logo_url: str | None = Field(default=None, description="URL to the restaurant logo image")
    whatsapp_number: str | None = Field(default=None, min_length=7, max_length=20)
    instagram_url: str | None = Field(default=None, max_length=200, description="Instagram profile URL or username")
    phone_number: str | None = Field(default=None, max_length=20, description="Direct call phone number")
    currency_code: Literal["IQD", "USD"] | None = Field(default=None, description="رمز العملة: IQD (دينار عراقي) أو USD (دولار أمريكي)")
    delivery_info: DeliveryInfo | None = Field(default=None, description="Delivery configuration")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "The Golden Fork — Updated",
            "description": "Now serving breakfast too!",
            "logo_url": "https://cdn.example.com/logos/golden-fork.png",
            "whatsapp_number": "966509999999",
        }
    })


def restaurant_from_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert a MongoDB restaurant document to a plain dict with id as string."""
    doc = doc.copy()
    doc["id"] = str(doc.pop("_id"))
    return doc
