from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ItemVariant(BaseModel):
    """A named size/type variant of a menu item with its own price."""
    name: str = Field(..., min_length=1, max_length=100, description="Variant label, e.g. 'وجبة عادية'")
    price: Decimal = Field(..., gt=0, decimal_places=2, description="Price for this variant")


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------

class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, description="Category display name")
    description: str | None = Field(default=None, max_length=300)
    image_url: str | None = Field(default=None, description="Optional header image for the category")
    order: int = Field(default=0, ge=0, description="Display position (lower = first)")

    model_config = ConfigDict(json_schema_extra={
        "example": {"name": "Starters", "description": "Light bites to begin your meal", "order": 0}
    })


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=300)
    image_url: str | None = None
    order: int | None = Field(default=None, ge=0)
    is_active: bool | None = Field(default=None, description="Set false to hide the category from the public menu")

    model_config = ConfigDict(json_schema_extra={
        "example": {"name": "Appetisers", "is_active": True, "order": 1}
    })


class CategoryResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    image_url: str | None = None
    restaurant_id: str
    order: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id": "664f1c2e8a1b2c3d4e5f0010",
            "name": "Starters",
            "description": "Light bites to begin your meal",
            "image_url": None,
            "restaurant_id": "664f1c2e8a1b2c3d4e5f6a7b",
            "order": 0,
            "is_active": True,
            "created_at": "2024-06-01T10:00:00Z",
        }
    })


class CategoryInDB(CategoryResponse):
    pass


# ---------------------------------------------------------------------------
# MenuItem
# ---------------------------------------------------------------------------

class MenuItemCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=150, description="Item display name")
    description: str | None = Field(default=None, max_length=500)
    price: Decimal = Field(..., gt=0, decimal_places=2, description="Base price (used when no variants are set)")
    image_url: str | None = Field(default=None, description="Optional item photo URL")
    category_id: str = Field(..., description="ID of the parent category")
    order: int = Field(default=0, ge=0, description="Display position within the category")
    variants: list[ItemVariant] = Field(default_factory=list, description="Optional size/type variants each with their own price")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "Classic Cheeseburger",
            "description": "Beef patty, cheddar, lettuce, tomato, pickles",
            "price": "13.50",
            "category_id": "664f1c2e8a1b2c3d4e5f0010",
            "order": 0,
        }
    })


class MenuItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=500)
    price: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    image_url: str | None = None
    category_id: str | None = Field(default=None, description="Move item to a different category")
    order: int | None = Field(default=None, ge=0)
    is_available: bool | None = Field(default=None, description="Set false to hide item from the public menu")
    variants: list[ItemVariant] | None = Field(default=None, description="Replace the full variants list (pass empty list to remove all variants)")

    model_config = ConfigDict(json_schema_extra={
        "example": {"price": "15.00", "is_available": True}
    })


class MenuItemResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    price: Decimal
    image_url: str | None = None
    category_id: str
    restaurant_id: str
    is_available: bool
    order: int
    variants: list[ItemVariant] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id": "664f1c2e8a1b2c3d4e5f0020",
            "name": "Classic Cheeseburger",
            "description": "Beef patty, cheddar, lettuce, tomato, pickles",
            "price": "13.50",
            "image_url": None,
            "category_id": "664f1c2e8a1b2c3d4e5f0010",
            "restaurant_id": "664f1c2e8a1b2c3d4e5f6a7b",
            "is_available": True,
            "order": 0,
            "created_at": "2024-06-01T10:00:00Z",
        }
    })


class MenuItemInDB(MenuItemResponse):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def category_from_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert a MongoDB category document to a plain dict with id as string."""
    doc = doc.copy()
    doc["id"] = str(doc.pop("_id"))
    return doc


def menu_item_from_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert a MongoDB menu item document to a plain dict with id as string."""
    doc = doc.copy()
    doc["id"] = str(doc.pop("_id"))
    doc.setdefault("variants", [])
    return doc
