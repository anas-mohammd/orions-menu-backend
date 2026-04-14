from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis

from app.core.dependencies import get_db, get_redis, require_restaurant_owner
from app.db.redis import invalidate_menu_cache
from app.models.offer import OfferCreate, OfferResponse, OfferUpdate, offer_from_doc
from app.models.user import UserResponse

router = APIRouter()

_401 = {401: {"description": "Missing or invalid token"}}
_403 = {403: {"description": "restaurant_owner role required"}}
_404 = {404: {"description": "Offer or restaurant not found"}}


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
    from bson import ObjectId as _ObjId
    if restaurant_id:
        try:
            oid = _ObjId(restaurant_id)
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


async def _get_offer_or_404(
    db: AsyncIOMotorDatabase, offer_id: str, restaurant_id: str
) -> dict:
    try:
        oid = ObjectId(offer_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found")

    doc = await db["offers"].find_one({"_id": oid, "restaurant_id": restaurant_id})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Offer not found")
    return doc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=list[OfferResponse], summary="List offers", description="Returns all offers for the current owner's restaurant, newest first.", responses={**_401, **_403})
async def list_offers(
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID when owner has multiple"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[OfferResponse]:
    """Return all offers for the current owner's restaurant."""
    restaurant_id, _ = await _get_restaurant_or_404(db, current_user.id, restaurant_id)
    cursor = db["offers"].find({"restaurant_id": restaurant_id}).sort("created_at", -1)
    docs = await cursor.to_list(length=None)
    return [OfferResponse(**offer_from_doc(doc)) for doc in docs]


@router.post("", response_model=OfferResponse, status_code=status.HTTP_201_CREATED, summary="Create an offer", description="Creates a discount offer. Set `applicable_items` to a list of MenuItem IDs for item-specific discounts, or leave empty for a store-wide offer. All referenced items must belong to this restaurant.", responses={**_401, **_403, **_404})
async def create_offer(
    payload: OfferCreate,
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID when owner has multiple"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> OfferResponse:
    """Add a new offer to the current owner's restaurant."""
    restaurant_id, slug = await _get_restaurant_or_404(db, current_user.id, restaurant_id)

    # Verify all referenced items belong to this restaurant
    if payload.applicable_items:
        for item_id in payload.applicable_items:
            try:
                oid = ObjectId(item_id)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid item id: {item_id}",
                )
            item = await db["menu_items"].find_one(
                {"_id": oid, "restaurant_id": restaurant_id}, {"_id": 1}
            )
            if item is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Menu item {item_id} not found in your restaurant",
                )

    doc = {
        "title": payload.title,
        "description": payload.description,
        "discount_type": payload.discount_type.value,
        "discount_value": float(payload.discount_value),
        "applicable_items": payload.applicable_items,
        "start_date": payload.start_date,
        "end_date": payload.end_date,
        "restaurant_id": restaurant_id,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    result = await db["offers"].insert_one(doc)
    doc["_id"] = result.inserted_id

    await invalidate_menu_cache(slug, redis)
    return OfferResponse(**offer_from_doc(doc))


@router.put("/{offer_id}", response_model=OfferResponse, summary="Update an offer", description="Partially updates an offer. Date ordering (`end_date > start_date`) is validated even for partial updates by merging with existing values.", responses={**_401, **_403, **_404})
async def update_offer(
    offer_id: str,
    payload: OfferUpdate,
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> OfferResponse:
    """Update an existing offer."""
    restaurant_id, slug = await _get_restaurant_or_404(db, current_user.id)
    doc = await _get_offer_or_404(db, offer_id, restaurant_id)

    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields provided to update",
        )

    # Validate any new applicable_items belong to this restaurant
    if "applicable_items" in updates:
        for item_id in updates["applicable_items"]:
            try:
                oid = ObjectId(item_id)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid item id: {item_id}",
                )
            item = await db["menu_items"].find_one(
                {"_id": oid, "restaurant_id": restaurant_id}, {"_id": 1}
            )
            if item is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Menu item {item_id} not found in your restaurant",
                )

    # Validate date ordering when either date is updated
    start = updates.get("start_date", doc.get("start_date"))
    end = updates.get("end_date", doc.get("end_date"))
    if start and end and end <= start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must be after start_date",
        )

    # Serialise enum to string and Decimal to float for storage
    if "discount_type" in updates:
        updates["discount_type"] = updates["discount_type"].value
    if "discount_value" in updates:
        updates["discount_value"] = float(updates["discount_value"])

    await db["offers"].update_one({"_id": doc["_id"]}, {"$set": updates})
    doc.update(updates)

    await invalidate_menu_cache(slug, redis)
    return OfferResponse(**offer_from_doc(doc))


@router.delete("/{offer_id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT, summary="Delete an offer", description="Permanently deletes an offer.", responses={**_401, **_403, **_404})
async def delete_offer(
    offer_id: str,
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    """Delete an offer."""
    restaurant_id, slug = await _get_restaurant_or_404(db, current_user.id)
    doc = await _get_offer_or_404(db, offer_id, restaurant_id)
    await db["offers"].delete_one({"_id": doc["_id"]})
    await invalidate_menu_cache(slug, redis)


@router.put("/{offer_id}/toggle", response_model=OfferResponse, summary="Toggle offer active state", description="Flips `is_active`. Inactive offers are excluded from the public menu response.", responses={**_401, **_403, **_404})
async def toggle_offer(
    offer_id: str,
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> OfferResponse:
    """Flip is_active for an offer."""
    restaurant_id, slug = await _get_restaurant_or_404(db, current_user.id)
    doc = await _get_offer_or_404(db, offer_id, restaurant_id)

    new_active = not doc["is_active"]
    await db["offers"].update_one(
        {"_id": doc["_id"]},
        {"$set": {"is_active": new_active}},
    )
    doc["is_active"] = new_active

    await invalidate_menu_cache(slug, redis)
    return OfferResponse(**offer_from_doc(doc))
