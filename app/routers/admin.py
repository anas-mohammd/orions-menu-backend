from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, ConfigDict, Field

from app.core.dependencies import get_db, require_saas_admin
from app.models.restaurant import (
    Restaurant,
    RestaurantCreate,
    SubscriptionPlan,
    SubscriptionStatus,
    restaurant_from_doc,
)
from app.models.user import UserCreate, UserResponse, UserRole, user_from_doc
from app.utils.slugify import generate_slug
from app.core.security import hash_password

router = APIRouter()

_401 = {401: {"description": "Missing or invalid token"}}
_403 = {403: {"description": "saas_admin role required"}}
_404 = {404: {"description": "Resource not found"}}


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class SubscriptionUpdate(BaseModel):
    expires_at: datetime = Field(..., description="New subscription expiry date")
    subscription_plan: SubscriptionPlan = Field(default=SubscriptionPlan.monthly, description="Subscription plan type")
    subscription_price: float = Field(default=0.0, ge=0, description="Subscription price in IQD")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "expires_at": "2026-12-31T23:59:59Z",
            "subscription_plan": "yearly",
            "subscription_price": 150000,
        }
    })


class StatsResponse(BaseModel):
    total_restaurants: int
    active_subscriptions: int
    expired_subscriptions: int
    suspended_subscriptions: int

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "total_restaurants": 42,
            "active_subscriptions": 35,
            "expired_subscriptions": 5,
            "suspended_subscriptions": 2,
        }
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_subscription_status(expires_at: datetime) -> SubscriptionStatus:
    if expires_at > datetime.now(timezone.utc):
        return SubscriptionStatus.active
    return SubscriptionStatus.expired


async def _get_restaurant_doc_or_404(db: AsyncIOMotorDatabase, restaurant_id: str) -> dict:
    try:
        oid = ObjectId(restaurant_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Restaurant not found")

    doc = await db["restaurants"].find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Restaurant not found")
    return doc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/users",
    response_model=list[UserResponse],
    summary="List all users",
    description="Returns all users. Pass `role` to filter by role.",
    responses={**_401, **_403},
)
async def list_users(
    role: str | None = Query(default=None, description="Filter by role"),
    _admin: UserResponse = Depends(require_saas_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[UserResponse]:
    query: dict = {}
    if role:
        query["role"] = role
    cursor = db["users"].find(query).sort("created_at", -1)
    docs = await cursor.to_list(length=200)
    return [UserResponse(**user_from_doc(doc)) for doc in docs]


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user",
    description="Creates a new user with any role. Only saas_admin can call this.",
    responses={**_401, **_403, 409: {"description": "Email already registered"}},
)
async def create_user(
    payload: UserCreate,
    _admin: UserResponse = Depends(require_saas_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> UserResponse:
    existing = await db["users"].find_one({"email": payload.email})
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    doc = {
        "name": payload.name,
        "email": payload.email,
        "hashed_password": hash_password(payload.password),
        "role": payload.role.value,
        "created_at": datetime.now(timezone.utc),
    }
    result = await db["users"].insert_one(doc)
    doc["_id"] = result.inserted_id
    return UserResponse(**user_from_doc(doc))


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user",
    description="Permanently deletes a user. Cannot delete yourself.",
    responses={**_401, **_403, **_404},
)
async def delete_user(
    user_id: str,
    _admin: UserResponse = Depends(require_saas_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> None:
    if user_id == _admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete your own account")
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    result = await db["users"].delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


@router.get(
    "/restaurants",
    response_model=list[Restaurant],
    summary="List all restaurants",
    description="Returns all restaurants on the platform, newest first. Supports offset pagination via `skip` and `limit`.",
    responses={**_401, **_403},
)
async def list_restaurants(
    skip: int = Query(default=0, ge=0, description="Number of records to skip"),
    limit: int = Query(default=20, ge=1, le=100, description="Max records to return (1–100)"),
    _admin: UserResponse = Depends(require_saas_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[Restaurant]:
    cursor = db["restaurants"].find().skip(skip).limit(limit).sort("created_at", -1)
    docs = await cursor.to_list(length=limit)
    return [Restaurant(**restaurant_from_doc(doc)) for doc in docs]


@router.get(
    "/restaurants/{restaurant_id}",
    response_model=Restaurant,
    summary="Get restaurant details",
    description="Returns full details for a single restaurant including subscription status.",
    responses={**_401, **_403, **_404},
)
async def get_restaurant(
    restaurant_id: str,
    _admin: UserResponse = Depends(require_saas_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Restaurant:
    doc = await _get_restaurant_doc_or_404(db, restaurant_id)
    return Restaurant(**restaurant_from_doc(doc))


@router.post(
    "/restaurants",
    response_model=Restaurant,
    status_code=status.HTTP_201_CREATED,
    summary="Create a restaurant",
    description=(
        "Creates a new restaurant and links it to an existing `restaurant_owner` user. "
        "The slug is auto-generated from the name (with a suffix if it collides). "
        "`subscription_status` is derived automatically from `expires_at`."
    ),
    responses={**_401, **_403, **_404},
)
async def create_restaurant(
    payload: RestaurantCreate,
    _admin: UserResponse = Depends(require_saas_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Restaurant:
    # Validate all owner IDs
    for oid_str in payload.owner_ids:
        try:
            oid = ObjectId(oid_str)
        except Exception:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid owner_id: {oid_str}")
        owner = await db["users"].find_one({"_id": oid})
        if owner is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Owner not found: {oid_str}")

    base_slug = generate_slug(payload.name)
    slug = base_slug
    counter = 1
    while await db["restaurants"].find_one({"slug": slug}):
        slug = f"{base_slug}-{counter}"
        counter += 1

    now = datetime.now(timezone.utc)
    subscription_status = _resolve_subscription_status(payload.expires_at)

    doc = {
        "name": payload.name,
        "slug": slug,
        "description": None,
        "logo_url": None,
        "whatsapp_number": payload.whatsapp_number,
        "owner_ids": payload.owner_ids,
        "subscription_start_date": now,
        "subscription_expires_at": payload.expires_at,
        "subscription_status": subscription_status.value,
        "subscription_plan": payload.subscription_plan.value,
        "subscription_price": payload.subscription_price,
        "is_active": True,
        "created_at": now,
    }
    result = await db["restaurants"].insert_one(doc)
    doc["_id"] = result.inserted_id

    return Restaurant(**restaurant_from_doc(doc))


class RestaurantOwnersUpdate(BaseModel):
    owner_ids: list[str] = Field(..., min_length=1, description="Complete list of owner user IDs")

    model_config = ConfigDict(json_schema_extra={
        "example": {"owner_ids": ["664f1c2e8a1b2c3d4e5f0001", "664f1c2e8a1b2c3d4e5f0002"]}
    })


@router.put(
    "/restaurants/{restaurant_id}/owners",
    response_model=Restaurant,
    summary="Update restaurant owners",
    description="Replace the full list of owners for a restaurant. At least one owner is required.",
    responses={**_401, **_403, **_404},
)
async def update_restaurant_owners(
    restaurant_id: str,
    payload: RestaurantOwnersUpdate,
    _admin: UserResponse = Depends(require_saas_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Restaurant:
    doc = await _get_restaurant_doc_or_404(db, restaurant_id)

    # Validate all owner IDs
    for oid_str in payload.owner_ids:
        try:
            oid = ObjectId(oid_str)
        except Exception:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid owner_id: {oid_str}")
        owner = await db["users"].find_one({"_id": oid})
        if owner is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Owner not found: {oid_str}")

    await db["restaurants"].update_one(
        {"_id": doc["_id"]},
        {"$set": {"owner_ids": payload.owner_ids}},
    )
    doc["owner_ids"] = payload.owner_ids

    return Restaurant(**restaurant_from_doc(doc))


@router.put(
    "/restaurants/{restaurant_id}/subscription",
    response_model=Restaurant,
    summary="Update subscription",
    description=(
        "Sets a new `expires_at` date. "
        "`subscription_status` is recalculated automatically: "
        "future date → `active`, past date → `expired`."
    ),
    responses={**_401, **_403, **_404},
)
async def update_subscription(
    restaurant_id: str,
    payload: SubscriptionUpdate,
    _admin: UserResponse = Depends(require_saas_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Restaurant:
    doc = await _get_restaurant_doc_or_404(db, restaurant_id)
    new_status = _resolve_subscription_status(payload.expires_at)

    await db["restaurants"].update_one(
        {"_id": doc["_id"]},
        {"$set": {
            "subscription_expires_at": payload.expires_at,
            "subscription_status": new_status.value,
            "subscription_plan": payload.subscription_plan.value,
            "subscription_price": payload.subscription_price,
        }},
    )
    doc["subscription_expires_at"] = payload.expires_at
    doc["subscription_status"] = new_status.value
    doc["subscription_plan"] = payload.subscription_plan.value
    doc["subscription_price"] = payload.subscription_price

    return Restaurant(**restaurant_from_doc(doc))


@router.put(
    "/restaurants/{restaurant_id}/toggle",
    response_model=Restaurant,
    summary="Toggle restaurant active state",
    description=(
        "Flips `is_active` for the restaurant. "
        "Inactive restaurants return 404 on the public menu endpoint "
        "so customers cannot access them."
    ),
    responses={**_401, **_403, **_404},
)
async def toggle_restaurant(
    restaurant_id: str,
    _admin: UserResponse = Depends(require_saas_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Restaurant:
    doc = await _get_restaurant_doc_or_404(db, restaurant_id)
    new_is_active = not doc["is_active"]

    await db["restaurants"].update_one(
        {"_id": doc["_id"]},
        {"$set": {"is_active": new_is_active}},
    )
    doc["is_active"] = new_is_active

    return Restaurant(**restaurant_from_doc(doc))


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="Platform statistics",
    description="Returns counts of total, active, expired, and suspended restaurant subscriptions.",
    responses={**_401, **_403},
)
async def get_stats(
    _admin: UserResponse = Depends(require_saas_admin),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> StatsResponse:
    total = await db["restaurants"].count_documents({})
    active = await db["restaurants"].count_documents({"subscription_status": SubscriptionStatus.active.value})
    expired = await db["restaurants"].count_documents({"subscription_status": SubscriptionStatus.expired.value})
    suspended = await db["restaurants"].count_documents({"subscription_status": SubscriptionStatus.suspended.value})

    return StatsResponse(
        total_restaurants=total,
        active_subscriptions=active,
        expired_subscriptions=expired,
        suspended_subscriptions=suspended,
    )
