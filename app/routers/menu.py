from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from app.core.dependencies import get_db, get_redis, require_restaurant_owner
from app.db.redis import invalidate_menu_cache
from app.models.menu import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    MenuItemCreate,
    MenuItemResponse,
    MenuItemUpdate,
    category_from_doc,
    menu_item_from_doc,
)
from app.models.user import UserResponse

router = APIRouter()

_401 = {401: {"description": "Missing or invalid token"}}
_403 = {403: {"description": "restaurant_owner role required"}}
_404 = {404: {"description": "Resource not found"}}
_409 = {409: {"description": "Conflict — e.g. category still has items"}}


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class ReorderEntry(BaseModel):
    id: str = Field(..., description="Category ID")
    order: int = Field(..., ge=0, description="New display position")


class ReorderRequest(BaseModel):
    categories: list[ReorderEntry]

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "categories": [
                {"id": "664f1c2e8a1b2c3d4e5f0010", "order": 0},
                {"id": "664f1c2e8a1b2c3d4e5f0011", "order": 1},
            ]
        }
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_restaurant_or_404(
    db: AsyncIOMotorDatabase,
    owner_id: str,
    restaurant_id: str | None = None,
) -> tuple[str, str]:
    """Return (restaurant_id_str, slug) for a restaurant the owner belongs to.

    If restaurant_id is supplied, fetch that specific restaurant and verify ownership.
    Otherwise fall back to the first restaurant that lists owner_id in owner_ids.
    """
    if restaurant_id:
        try:
            oid = ObjectId(restaurant_id)
        except Exception:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Restaurant not found")
        doc = await db["restaurants"].find_one(
            {"_id": oid, "owner_ids": owner_id}, {"_id": 1, "slug": 1}
        )
        if doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Restaurant not found or access denied",
            )
    else:
        doc = await db["restaurants"].find_one({"owner_ids": owner_id}, {"_id": 1, "slug": 1})
        if doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No restaurant found for this account",
            )
    return str(doc["_id"]), doc["slug"]


async def _get_category_or_404(
    db: AsyncIOMotorDatabase, category_id: str, restaurant_id: str
) -> dict:
    """Return category doc scoped to the restaurant, or raise 404."""
    try:
        oid = ObjectId(category_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    doc = await db["categories"].find_one({"_id": oid, "restaurant_id": restaurant_id})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    return doc


async def _get_item_or_404(
    db: AsyncIOMotorDatabase, item_id: str, restaurant_id: str
) -> dict:
    """Return menu item doc scoped to the restaurant, or raise 404."""
    try:
        oid = ObjectId(item_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")

    doc = await db["menu_items"].find_one({"_id": oid, "restaurant_id": restaurant_id})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return doc


# ===========================================================================
# Categories
# ===========================================================================

@router.get("/categories", response_model=list[CategoryResponse], summary="List categories", description="Returns all categories for the current owner's restaurant, sorted by `order` ascending.", responses={**_401, **_403})
async def list_categories(
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID when owner has multiple"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[CategoryResponse]:
    """Return all categories for the current owner's restaurant, sorted by order."""
    restaurant_id, _ = await _get_restaurant_or_404(db, current_user.id, restaurant_id)
    cursor = db["categories"].find({"restaurant_id": restaurant_id}).sort("order", 1)
    docs = await cursor.to_list(length=None)
    return [CategoryResponse(**category_from_doc(doc)) for doc in docs]


@router.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED, summary="Create a category", description="Adds a new category to the restaurant. `is_active` defaults to `true`.", responses={**_401, **_403})
async def create_category(
    payload: CategoryCreate,
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID when owner has multiple"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> CategoryResponse:
    """Add a new category to the current owner's restaurant."""
    restaurant_id, slug = await _get_restaurant_or_404(db, current_user.id, restaurant_id)

    doc = {
        "name": payload.name,
        "description": payload.description,
        "image_url": payload.image_url,
        "restaurant_id": restaurant_id,
        "order": payload.order,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    result = await db["categories"].insert_one(doc)
    doc["_id"] = result.inserted_id

    await invalidate_menu_cache(slug, redis)
    return CategoryResponse(**category_from_doc(doc))


@router.put("/categories/reorder", response_model=None, status_code=status.HTTP_204_NO_CONTENT, summary="Reorder categories", description="Bulk-updates the `order` field for a list of categories. Send all categories with their desired positions in a single request.", responses={**_401, **_403})
async def reorder_categories(
    payload: ReorderRequest,
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID when owner has multiple"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    """Bulk-update the order field for a list of categories."""
    restaurant_id, slug = await _get_restaurant_or_404(db, current_user.id, restaurant_id)

    for entry in payload.categories:
        try:
            oid = ObjectId(entry.id)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid category id: {entry.id}",
            )
        await db["categories"].update_one(
            {"_id": oid, "restaurant_id": restaurant_id},
            {"$set": {"order": entry.order}},
        )

    await invalidate_menu_cache(slug, redis)


@router.put("/categories/{category_id}", response_model=CategoryResponse, summary="Update a category", description="Partially updates a category. Only provided fields are changed. Set `is_active: false` to hide the category from the public menu.", responses={**_401, **_403, **_404})
async def update_category(
    category_id: str,
    payload: CategoryUpdate,
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID when owner has multiple"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> CategoryResponse:
    """Update an existing category."""
    restaurant_id, slug = await _get_restaurant_or_404(db, current_user.id, restaurant_id)
    doc = await _get_category_or_404(db, category_id, restaurant_id)

    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields provided to update",
        )

    await db["categories"].update_one({"_id": doc["_id"]}, {"$set": updates})
    doc.update(updates)

    await invalidate_menu_cache(slug, redis)
    return CategoryResponse(**category_from_doc(doc))


@router.delete("/categories/{category_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT, summary="Delete a category", description="Permanently deletes a category. Returns **409** if any menu items are still linked to it — move or delete the items first.", responses={**_401, **_403, **_404, **_409})
async def delete_category(
    category_id: str,
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID when owner has multiple"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    """Delete a category. Rejected if any menu items are linked to it."""
    restaurant_id, slug = await _get_restaurant_or_404(db, current_user.id, restaurant_id)
    doc = await _get_category_or_404(db, category_id, restaurant_id)

    items_count = await db["menu_items"].count_documents({"category_id": category_id})
    if items_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete category: {items_count} item(s) still linked to it",
        )

    await db["categories"].delete_one({"_id": doc["_id"]})
    await invalidate_menu_cache(slug, redis)


# ===========================================================================
# Items
# ===========================================================================

@router.get("/items", response_model=list[MenuItemResponse], summary="List menu items", description="Returns all items for the restaurant. Pass `category_id` to filter by category. Results are sorted by `(category_id, order)`.", responses={**_401, **_403})
async def list_items(
    category_id: str | None = Query(default=None),
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID when owner has multiple"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[MenuItemResponse]:
    """Return all items for the current owner's restaurant, optionally filtered by category."""
    restaurant_id, _ = await _get_restaurant_or_404(db, current_user.id, restaurant_id)

    query: dict = {"restaurant_id": restaurant_id}
    if category_id is not None:
        query["category_id"] = category_id

    cursor = db["menu_items"].find(query).sort([("category_id", 1), ("order", 1)])
    docs = await cursor.to_list(length=None)
    return [MenuItemResponse(**menu_item_from_doc(doc)) for doc in docs]


@router.post("/items", response_model=MenuItemResponse, status_code=status.HTTP_201_CREATED, summary="Create a menu item", description="Adds a new item under an existing category. The category must belong to the same restaurant. `is_available` defaults to `true`.", responses={**_401, **_403, **_404})
async def create_item(
    payload: MenuItemCreate,
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID when owner has multiple"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> MenuItemResponse:
    """Add a new menu item to the current owner's restaurant."""
    restaurant_id, slug = await _get_restaurant_or_404(db, current_user.id, restaurant_id)

    # Verify the target category belongs to this restaurant
    await _get_category_or_404(db, payload.category_id, restaurant_id)

    doc = {
        "name": payload.name,
        "description": payload.description,
        "price": float(payload.price),
        "image_url": payload.image_url,
        "category_id": payload.category_id,
        "restaurant_id": restaurant_id,
        "is_available": True,
        "order": payload.order,
        "variants": [{"name": v.name, "price": float(v.price)} for v in payload.variants],
        "created_at": datetime.now(timezone.utc),
    }
    result = await db["menu_items"].insert_one(doc)
    doc["_id"] = result.inserted_id

    await invalidate_menu_cache(slug, redis)
    return MenuItemResponse(**menu_item_from_doc(doc))


@router.put("/items/{item_id}", response_model=MenuItemResponse, summary="Update a menu item", description="Partially updates a menu item. When `category_id` is changed, the new category must belong to the same restaurant.", responses={**_401, **_403, **_404})
async def update_item(
    item_id: str,
    payload: MenuItemUpdate,
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID when owner has multiple"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> MenuItemResponse:
    """Update an existing menu item."""
    restaurant_id, slug = await _get_restaurant_or_404(db, current_user.id, restaurant_id)
    doc = await _get_item_or_404(db, item_id, restaurant_id)

    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields provided to update",
        )

    # Convert Decimal to float so BSON can encode it
    if "price" in updates:
        updates["price"] = float(updates["price"])

    # Serialize variants to plain dicts so BSON can encode them
    if "variants" in updates:
        updates["variants"] = [
            {"name": v["name"], "price": float(v["price"])} for v in updates["variants"]
        ]

    # If moving to a different category, verify the new category belongs to this restaurant
    if "category_id" in updates:
        await _get_category_or_404(db, updates["category_id"], restaurant_id)

    await db["menu_items"].update_one({"_id": doc["_id"]}, {"$set": updates})
    doc.update(updates)

    await invalidate_menu_cache(slug, redis)
    return MenuItemResponse(**menu_item_from_doc(doc))


@router.delete("/items/{item_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT, summary="Delete a menu item", description="Permanently deletes a menu item.", responses={**_401, **_403, **_404})
async def delete_item(
    item_id: str,
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID when owner has multiple"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    """Delete a menu item."""
    restaurant_id, slug = await _get_restaurant_or_404(db, current_user.id, restaurant_id)
    doc = await _get_item_or_404(db, item_id, restaurant_id)
    await db["menu_items"].delete_one({"_id": doc["_id"]})
    await invalidate_menu_cache(slug, redis)


@router.put("/items/{item_id}/toggle", response_model=MenuItemResponse, summary="Toggle item availability", description="Flips `is_available`. Unavailable items are hidden from the public menu and cannot be ordered.", responses={**_401, **_403, **_404})
async def toggle_item(
    item_id: str,
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID when owner has multiple"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> MenuItemResponse:
    """Flip is_available for a menu item."""
    restaurant_id, slug = await _get_restaurant_or_404(db, current_user.id, restaurant_id)
    doc = await _get_item_or_404(db, item_id, restaurant_id)

    new_available = not doc["is_available"]
    await db["menu_items"].update_one(
        {"_id": doc["_id"]},
        {"$set": {"is_available": new_available}},
    )
    doc["is_available"] = new_available

    await invalidate_menu_cache(slug, redis)
    return MenuItemResponse(**menu_item_from_doc(doc))
