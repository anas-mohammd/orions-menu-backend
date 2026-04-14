import json
from datetime import datetime, timezone
from decimal import Decimal

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from redis.asyncio import Redis

from app.core.dependencies import get_db, get_redis
from app.core.rate_limit import rate_limit
from app.models.offer import OfferResponse, offer_from_doc
from app.models.menu import CategoryResponse, MenuItemResponse, category_from_doc, menu_item_from_doc
from app.models.order import OrderCreate, OrderItem, OrderResponse, order_from_doc
from app.models.restaurant import SubscriptionStatus, restaurant_from_doc
from app.models.review import ReviewCreate, ReviewResponse, ReviewsListResponse, review_from_doc
from app.services.whatsapp_service import generate_whatsapp_link

router = APIRouter()

_MENU_CACHE_TTL = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class PublicRestaurantInfo(BaseModel):
    """Minimal restaurant info exposed to customers — no internal/admin fields."""
    name: str
    slug: str
    description: str | None = None
    logo_url: str | None = None
    whatsapp_number: str
    instagram_url: str | None = None
    phone_number: str | None = None
    currency_code: str = "SAR"


class PublicMenuResponse(BaseModel):
    restaurant: PublicRestaurantInfo
    categories: list[CategoryResponse]
    items: list[MenuItemResponse]
    offers: list[OfferResponse]


class OrderResult(BaseModel):
    whatsapp_link: str
    order_id: str
    total: Decimal
    discount_amount: Decimal = Decimal("0")
    original_total: Decimal


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(slug: str) -> str:
    return f"menu:{slug}"


def _to_json(data: dict) -> str:
    """Serialize a dict to JSON, converting Decimal and datetime to strings."""
    return json.dumps(data, default=str)


def _from_json(raw: str) -> dict:
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Endpoint helpers
# ---------------------------------------------------------------------------

async def _get_active_restaurant_or_error(db: AsyncIOMotorDatabase, slug: str) -> dict:
    """Fetch restaurant by slug, enforce is_active and subscription checks."""
    doc = await db["restaurants"].find_one({"slug": slug})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Restaurant not found")

    if not doc.get("is_active", False):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Restaurant not found")

    if doc.get("subscription_status") == SubscriptionStatus.expired.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This restaurant's subscription has expired",
        )

    # Lazily expire subscription if past the expiry date but status not updated yet
    expires_at: datetime = doc["subscription_expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        await db["restaurants"].update_one(
            {"_id": doc["_id"]},
            {"$set": {"subscription_status": SubscriptionStatus.expired.value}},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This restaurant's subscription has expired",
        )

    return doc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/menu/{slug}",
    response_model=PublicMenuResponse,
    summary="Get public menu",
    description=(
        "Returns the full menu for a restaurant: info, active categories, available items, "
        "and active non-expired offers. "
        "Response is **cached in Redis for 5 minutes** and invalidated automatically on any menu or offer change. "
        "Returns **403** if the subscription has expired, **404** if the restaurant is inactive or unknown."
    ),
    responses={
        403: {"description": "Subscription expired"},
        404: {"description": "Restaurant not found or inactive"},
    },
)
async def get_public_menu(
    slug: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> PublicMenuResponse:
    cache_key = _cache_key(slug)

    # Try cache first
    cached = await redis.get(cache_key)
    if cached:
        data = _from_json(cached)
        return PublicMenuResponse(**data)

    # Fetch and validate restaurant
    restaurant_doc = await _get_active_restaurant_or_error(db, slug)
    restaurant_id = str(restaurant_doc["_id"])

    # Fetch active categories sorted by order
    cat_cursor = db["categories"].find(
        {"restaurant_id": restaurant_id, "is_active": True}
    ).sort("order", 1)
    category_docs = await cat_cursor.to_list(length=None)

    # Fetch available items sorted by order
    item_cursor = db["menu_items"].find(
        {"restaurant_id": restaurant_id, "is_available": True}
    ).sort([("category_id", 1), ("order", 1)])
    item_docs = await item_cursor.to_list(length=None)

    # Fetch active, non-expired offers
    now = datetime.now(timezone.utc)
    offer_cursor = db["offers"].find({
        "restaurant_id": restaurant_id,
        "is_active": True,
        "end_date": {"$gt": now},
    })
    offer_docs = await offer_cursor.to_list(length=None)

    response = PublicMenuResponse(
        restaurant=PublicRestaurantInfo(
            name=restaurant_doc["name"],
            slug=restaurant_doc["slug"],
            description=restaurant_doc.get("description"),
            logo_url=restaurant_doc.get("logo_url"),
            whatsapp_number=restaurant_doc["whatsapp_number"],
            instagram_url=restaurant_doc.get("instagram_url"),
            phone_number=restaurant_doc.get("phone_number"),
            currency_code=restaurant_doc.get("currency_code", "SAR"),
        ),
        categories=[CategoryResponse(**category_from_doc(d)) for d in category_docs],
        items=[MenuItemResponse(**menu_item_from_doc(d)) for d in item_docs],
        offers=[OfferResponse(**offer_from_doc(d)) for d in offer_docs],
    )

    # Store in cache
    await redis.setex(cache_key, _MENU_CACHE_TTL, _to_json(response.model_dump(mode="json")))

    return response


@router.post(
    "/menu/{slug}/order",
    response_model=OrderResult,
    status_code=status.HTTP_201_CREATED,
    summary="Place an order",
    description=(
        "Submits a customer order. Item prices are **always fetched from the database** — "
        "the request carries only item IDs and quantities. "
        "Returns a pre-filled **WhatsApp deep-link** the customer taps to send the order. "
        "The order is also persisted to the database. "
        "Rate limited to **10 requests per minute** per IP."
    ),
    responses={
        403: {"description": "Subscription expired"},
        404: {"description": "Restaurant inactive, or one of the requested items is unavailable"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def place_order(
    slug: str,
    payload: OrderCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
    _: None = Depends(rate_limit(max_requests=10, window_seconds=60, key_prefix="order")),
) -> OrderResult:
    restaurant_doc = await _get_active_restaurant_or_error(db, slug)
    restaurant_id = str(restaurant_doc["_id"])

    # Resolve each ordered item from the DB — never trust client-side prices
    order_items: list[OrderItem] = []
    for entry in payload.items:
        try:
            oid = ObjectId(entry.item_id)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid item id: {entry.item_id}",
            )

        item_doc = await db["menu_items"].find_one({
            "_id": oid,
            "restaurant_id": restaurant_id,
            "is_available": True,
        })
        if item_doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Item {entry.item_id} is not available",
            )

        variants = item_doc.get("variants", [])
        if variants and entry.variant_name:
            # Find the matching variant price
            variant = next((v for v in variants if v["name"] == entry.variant_name), None)
            if variant is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Variant '{entry.variant_name}' not found for item {entry.item_id}",
                )
            price = Decimal(str(variant["price"]))
        elif variants and not entry.variant_name:
            # Default to the first variant if none specified
            price = Decimal(str(variants[0]["price"]))
        else:
            price = Decimal(str(item_doc["price"]))

        subtotal = price * entry.quantity
        order_items.append(OrderItem(
            item_id=entry.item_id,
            name=item_doc["name"],
            price=price,
            quantity=entry.quantity,
            subtotal=subtotal,
            variant_name=entry.variant_name,
        ))

    original_total = sum(item.subtotal for item in order_items)

    # Apply active, non-expired offers
    now = datetime.now(timezone.utc)
    offer_cursor = db["offers"].find({
        "restaurant_id": restaurant_id,
        "is_active": True,
        "start_date": {"$lte": now},
        "end_date": {"$gt": now},
    })
    offer_docs = await offer_cursor.to_list(length=None)

    discount_amount = Decimal("0")
    for offer_doc in offer_docs:
        disc_type = offer_doc["discount_type"]
        disc_value = Decimal(str(offer_doc["discount_value"]))
        applicable_items = offer_doc.get("applicable_items", [])

        if not applicable_items:
            # Store-wide: apply to the full original total
            base = original_total
        else:
            # Item-specific: apply only to matching items' subtotals
            applicable_set = set(applicable_items)
            base = sum(
                item.subtotal for item in order_items
                if item.item_id in applicable_set
            )

        if disc_type == "percentage":
            discount_amount += base * disc_value / Decimal("100")
        else:  # fixed_amount
            discount_amount += min(disc_value, base)

    # Cap discount so total never goes negative
    discount_amount = min(discount_amount, original_total)
    total = original_total - discount_amount

    # Build WhatsApp message and link
    whatsapp_link = generate_whatsapp_link(
        whatsapp_number=restaurant_doc["whatsapp_number"],
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        items=order_items,
        total=total,
        notes=payload.notes,
        currency_code=restaurant_doc.get("currency_code", "SAR"),
        discount_amount=discount_amount,
        delivery_info=restaurant_doc.get("delivery_info"),
    )

    # Persist the order
    order_doc = {
        "restaurant_id": restaurant_id,
        "customer_name": payload.customer_name,
        "customer_phone": payload.customer_phone,
        "items": [
            {
                **item.model_dump(),
                "price": float(item.price),
                "subtotal": float(item.subtotal),
            }
            for item in order_items
        ],
        "notes": payload.notes,
        "original_total": float(original_total),
        "discount_amount": float(discount_amount),
        "total": float(total),
        "whatsapp_link": whatsapp_link,
        "created_at": now,
    }
    result = await db["orders"].insert_one(order_doc)

    return OrderResult(
        whatsapp_link=whatsapp_link,
        order_id=str(result.inserted_id),
        total=total,
        discount_amount=discount_amount,
        original_total=original_total,
    )


@router.get(
    "/menu/{slug}/reviews",
    response_model=ReviewsListResponse,
    summary="Get restaurant reviews",
    description="Returns all customer reviews for the restaurant with average rating.",
    responses={
        404: {"description": "Restaurant not found or inactive"},
    },
)
async def get_reviews(
    slug: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> ReviewsListResponse:
    restaurant_doc = await _get_active_restaurant_or_error(db, slug)
    restaurant_id = str(restaurant_doc["_id"])

    cursor = db["reviews"].find({"restaurant_id": restaurant_id}).sort("created_at", -1)
    review_docs = await cursor.to_list(length=None)

    reviews = [ReviewResponse(**review_from_doc(d)) for d in review_docs]
    total = len(reviews)
    average_rating = round(sum(r.rating for r in reviews) / total, 1) if total else 0.0

    return ReviewsListResponse(reviews=reviews, total=total, average_rating=average_rating)


@router.post(
    "/menu/{slug}/reviews",
    response_model=ReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a review",
    description="Submits a customer review with a 1–5 star rating and optional comment. Rate limited to 3 reviews per minute per IP.",
    responses={
        403: {"description": "Subscription expired"},
        404: {"description": "Restaurant not found or inactive"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def submit_review(
    slug: str,
    payload: ReviewCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: None = Depends(rate_limit(max_requests=3, window_seconds=60, key_prefix="review")),
) -> ReviewResponse:
    restaurant_doc = await _get_active_restaurant_or_error(db, slug)
    restaurant_id = str(restaurant_doc["_id"])

    now = datetime.now(timezone.utc)
    doc = {
        "restaurant_id": restaurant_id,
        "customer_name": payload.customer_name,
        "rating": payload.rating,
        "comment": payload.comment,
        "created_at": now,
    }
    result = await db["reviews"].insert_one(doc)
    doc["_id"] = result.inserted_id

    return ReviewResponse(**review_from_doc(doc))
