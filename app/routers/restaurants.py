from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, ConfigDict, Field

from app.core.dependencies import get_db, get_redis, require_restaurant_owner
from app.db.redis import invalidate_menu_cache
from app.models.restaurant import Restaurant, RestaurantUpdate, restaurant_from_doc
from app.models.review import ReviewsListResponse, review_from_doc, ReviewResponse
from app.models.user import UserResponse
from app.utils.qr_generator import generate_qr_code
from redis.asyncio import Redis

router = APIRouter()

_401 = {401: {"description": "Missing or invalid token"}}
_403 = {403: {"description": "restaurant_owner role required"}}
_404 = {404: {"description": "No restaurant linked to this account"}}


class QRCodeResponse(BaseModel):
    slug: str = Field(..., description="Restaurant slug")
    menu_url: str = Field(..., description="Full URL to the public menu page")
    qr_base64: str = Field(..., description="Base64-encoded PNG of the QR code (no data-URI prefix)")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "slug": "the-golden-fork",
            "menu_url": "http://localhost:8000/public/menu/the-golden-fork",
            "qr_base64": "iVBORw0KGgoAAAANSUhEUgAA...",
        }
    })


async def _get_owner_restaurant_or_404(
    db: AsyncIOMotorDatabase,
    owner_id: str,
    restaurant_id: str | None = None,
) -> dict:
    """Return a restaurant doc the owner belongs to.

    If restaurant_id is supplied, fetch that specific restaurant and verify ownership.
    Otherwise fall back to the first restaurant that lists owner_id in owner_ids.
    """
    if restaurant_id:
        try:
            oid = ObjectId(restaurant_id)
        except Exception:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="المطعم غير موجود")
        doc = await db["restaurants"].find_one({"_id": oid, "owner_ids": owner_id})
        if doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="المطعم غير موجود أو ليس لديك صلاحية",
            )
    else:
        doc = await db["restaurants"].find_one({"owner_ids": owner_id})
        if doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="لا يوجد مطعم مرتبط بهذا الحساب",
            )
    return doc


@router.get(
    "/my-restaurants",
    response_model=list[Restaurant],
    summary="List my restaurants",
    description="Returns all restaurants where the authenticated owner is listed in `owner_ids`.",
    responses={**_401, **_403},
)
async def list_my_restaurants(
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> list[Restaurant]:
    cursor = db["restaurants"].find({"owner_ids": current_user.id})
    docs = await cursor.to_list(length=None)
    return [Restaurant(**restaurant_from_doc(doc)) for doc in docs]


@router.get(
    "/me/qr",
    response_model=QRCodeResponse,
    summary="Get menu QR code",
    description=(
        "Generates a QR code image (PNG, base64) pointing to the restaurant's public menu URL. "
        "Pass `restaurant_id` to target a specific restaurant when the owner manages multiple."
    ),
    responses={**_401, **_403, **_404},
)
async def get_my_qr_code(
    request: Request,
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID (required when owner has multiple)"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> QRCodeResponse:
    doc = await _get_owner_restaurant_or_404(db, current_user.id, restaurant_id)
    slug = doc["slug"]

    base_url = str(request.base_url).rstrip("/")
    menu_url = f"{base_url}/public/menu/{slug}"
    qr_base64 = generate_qr_code(slug, f"{base_url}/public")

    return QRCodeResponse(slug=slug, menu_url=menu_url, qr_base64=qr_base64)


@router.get(
    "/me",
    response_model=Restaurant,
    summary="Get my restaurant",
    description="Returns full details for the restaurant. Pass `restaurant_id` when managing multiple restaurants.",
    responses={**_401, **_403, **_404},
)
async def get_my_restaurant(
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID (required when owner has multiple)"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Restaurant:
    doc = await _get_owner_restaurant_or_404(db, current_user.id, restaurant_id)
    return Restaurant(**restaurant_from_doc(doc))


@router.put(
    "/me",
    response_model=Restaurant,
    summary="Update my restaurant",
    description=(
        "Updates the allowed fields of the restaurant. All fields are optional — "
        "only the provided fields are changed. "
        "`slug`, subscription fields, and `owner_ids` are **immutable** and cannot be changed here. "
        "Pass `restaurant_id` when managing multiple restaurants."
    ),
    responses={**_401, **_403, **_404},
)
async def update_my_restaurant(
    payload: RestaurantUpdate,
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID (required when owner has multiple)"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Restaurant:
    doc = await _get_owner_restaurant_or_404(db, current_user.id, restaurant_id)

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="لم يتم تحديد أي حقل للتعديل",
        )

    await db["restaurants"].update_one({"_id": doc["_id"]}, {"$set": updates})
    doc.update(updates)

    # Invalidate public menu cache so customers see the latest restaurant info
    await invalidate_menu_cache(doc["slug"], redis)

    return Restaurant(**restaurant_from_doc(doc))


@router.get(
    "/me/reviews",
    response_model=ReviewsListResponse,
    summary="Get my restaurant reviews",
    description="Returns all customer reviews for the owner's restaurant, sorted newest first.",
    responses={**_401, **_403, **_404},
)
async def get_my_reviews(
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID (required when owner has multiple)"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> ReviewsListResponse:
    doc = await _get_owner_restaurant_or_404(db, current_user.id, restaurant_id)
    rid = str(doc["_id"])

    cursor = db["reviews"].find({"restaurant_id": rid}).sort("created_at", -1)
    review_docs = await cursor.to_list(length=None)

    reviews = [ReviewResponse(**review_from_doc(d)) for d in review_docs]
    total = len(reviews)
    average_rating = round(sum(r.rating for r in reviews) / total, 1) if total else 0.0

    return ReviewsListResponse(reviews=reviews, total=total, average_rating=average_rating)


@router.delete(
    "/me/reviews/{review_id}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a review",
    description="Permanently deletes a customer review from the owner's restaurant.",
    responses={**_401, **_403, **_404},
)
async def delete_review(
    review_id: str,
    restaurant_id: str | None = Query(default=None),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> None:
    doc = await _get_owner_restaurant_or_404(db, current_user.id, restaurant_id)
    rid = str(doc["_id"])

    try:
        oid = ObjectId(review_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="التقييم غير موجود")

    result = await db["reviews"].delete_one({"_id": oid, "restaurant_id": rid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="التقييم غير موجود")
