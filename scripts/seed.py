"""
Seed script — populates the database with a saas_admin, a restaurant owner,
a sample restaurant, categories, menu items, and offers.

Run from the backend/ directory:
    python scripts/seed.py

The script is idempotent: re-running it skips records that already exist.
Credentials are read from the .env file (see .env.example for the keys).
"""

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

# Allow imports from the project root (backend/)
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.security import hash_password
from app.models.user import UserRole
from app.utils.slugify import generate_slug

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

MONGODB_URL    = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DB_NAME        = os.getenv("MONGODB_DB_NAME", "saas_menu")
ADMIN_EMAIL    = os.getenv("SEED_ADMIN_EMAIL", "admin@orionmenu.com")
ADMIN_PASSWORD = os.getenv("SEED_ADMIN_PASSWORD", "Admin123!")
OWNER_EMAIL    = os.getenv("SEED_OWNER_EMAIL", "owner@orionmenu.com")
OWNER_PASSWORD = os.getenv("SEED_OWNER_PASSWORD", "Owner123!")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _upsert_user(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    email: str,
    password: str,
    role: UserRole,
) -> str:
    """Insert the user if the email is not already taken; return the user_id."""
    existing = await db["users"].find_one({"email": email})
    if existing:
        print(f"  [skip] user already exists: {email}")
        return str(existing["_id"])

    doc = {
        "name": name,
        "email": email,
        "hashed_password": hash_password(password),
        "role": role.value,
        "created_at": _now(),
    }
    result = await db["users"].insert_one(doc)
    print(f"  [ok]   created {role.value}: {email}")
    return str(result.inserted_id)


async def _upsert_restaurant(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    whatsapp_number: str,
    owner_id: str,
    description: str,
    expires_days: int = 365,
) -> tuple[str, str]:
    """Insert the restaurant if the owner doesn't already have one; return (id, slug)."""
    existing = await db["restaurants"].find_one({"owner_ids": owner_id})
    if existing:
        print(f"  [skip] restaurant already exists for owner {owner_id}: {existing['slug']}")
        return str(existing["_id"]), existing["slug"]

    base_slug = generate_slug(name)
    slug = base_slug
    counter = 1
    while await db["restaurants"].find_one({"slug": slug}):
        slug = f"{base_slug}-{counter}"
        counter += 1

    now = _now()
    doc = {
        "name": name,
        "slug": slug,
        "description": description,
        "logo_url": None,
        "whatsapp_number": whatsapp_number,
        "owner_ids": [owner_id],
        "subscription_start_date": now,
        "subscription_expires_at": now + timedelta(days=expires_days),
        "subscription_status": "active",
        "is_active": True,
        "created_at": now,
    }
    result = await db["restaurants"].insert_one(doc)
    print(f"  [ok]   created restaurant: {name!r} (slug: {slug})")
    return str(result.inserted_id), slug


async def _upsert_category(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    description: str,
    restaurant_id: str,
    order: int,
) -> str:
    """Insert a category if it doesn't already exist for this restaurant; return id."""
    existing = await db["categories"].find_one(
        {"restaurant_id": restaurant_id, "name": name}
    )
    if existing:
        return str(existing["_id"])

    doc = {
        "name": name,
        "description": description,
        "image_url": None,
        "restaurant_id": restaurant_id,
        "order": order,
        "is_active": True,
        "created_at": _now(),
    }
    result = await db["categories"].insert_one(doc)
    print(f"    [ok] category: {name!r}")
    return str(result.inserted_id)


async def _upsert_item(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    description: str,
    price: float,
    category_id: str,
    restaurant_id: str,
    order: int,
) -> None:
    existing = await db["menu_items"].find_one(
        {"restaurant_id": restaurant_id, "name": name}
    )
    if existing:
        return

    doc = {
        "name": name,
        "description": description,
        "price": price,
        "image_url": None,
        "category_id": category_id,
        "restaurant_id": restaurant_id,
        "is_available": True,
        "order": order,
        "created_at": _now(),
    }
    await db["menu_items"].insert_one(doc)
    print(f"      [ok] item: {name!r} — {price:.2f}")


async def _upsert_offer(
    db: AsyncIOMotorDatabase,
    *,
    title: str,
    description: str,
    discount_type: str,
    discount_value: float,
    restaurant_id: str,
    days_from_now: int = 30,
) -> None:
    existing = await db["offers"].find_one(
        {"restaurant_id": restaurant_id, "title": title}
    )
    if existing:
        return

    now = _now()
    doc = {
        "title": title,
        "description": description,
        "discount_type": discount_type,
        "discount_value": discount_value,
        "applicable_items": [],
        "start_date": now,
        "end_date": now + timedelta(days=days_from_now),
        "restaurant_id": restaurant_id,
        "is_active": True,
        "created_at": now,
    }
    await db["offers"].insert_one(doc)
    print(f"    [ok] offer: {title!r}")


# ---------------------------------------------------------------------------
# Main seed routine
# ---------------------------------------------------------------------------

async def seed() -> None:
    client = AsyncIOMotorClient(MONGODB_URL)
    db = client[DB_NAME]

    print(f"\n{'='*50}")
    print(f"  Seeding database: {DB_NAME}")
    print(f"{'='*50}\n")

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    print("► Users")

    admin_id = await _upsert_user(
        db,
        name="Platform Admin",
        email=ADMIN_EMAIL,
        password=ADMIN_PASSWORD,
        role=UserRole.saas_admin,
    )

    owner_id = await _upsert_user(
        db,
        name="Ahmed Al-Rashid",
        email=OWNER_EMAIL,
        password=OWNER_PASSWORD,
        role=UserRole.restaurant_owner,
    )

    # ------------------------------------------------------------------
    # Restaurant
    # ------------------------------------------------------------------
    print("\n► Restaurant")

    restaurant_id, slug = await _upsert_restaurant(
        db,
        name="The Golden Fork",
        whatsapp_number="966501234567",
        owner_id=owner_id,
        description="A cozy spot serving classic comfort food with a modern twist.",
    )

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------
    print("\n► Categories & Items")

    starters_id = await _upsert_category(
        db, name="Starters", description="Light bites to begin your meal",
        restaurant_id=restaurant_id, order=0,
    )
    mains_id = await _upsert_category(
        db, name="Mains", description="Hearty main dishes",
        restaurant_id=restaurant_id, order=1,
    )
    desserts_id = await _upsert_category(
        db, name="Desserts", description="Sweet endings",
        restaurant_id=restaurant_id, order=2,
    )
    drinks_id = await _upsert_category(
        db, name="Drinks", description="Hot and cold beverages",
        restaurant_id=restaurant_id, order=3,
    )

    # ------------------------------------------------------------------
    # Menu items
    # ------------------------------------------------------------------

    starters = [
        ("Garlic Bread",        "Toasted sourdough with roasted garlic butter",    4.50),
        ("Chicken Wings (6pc)", "Crispy wings with choice of BBQ or buffalo sauce", 9.00),
        ("Soup of the Day",     "Ask your server for today's selection",            5.50),
    ]
    for i, (name, desc, price) in enumerate(starters):
        await _upsert_item(db, name=name, description=desc, price=price,
                           category_id=starters_id, restaurant_id=restaurant_id, order=i)

    mains = [
        ("Classic Cheeseburger", "Beef patty, cheddar, lettuce, tomato, pickles",   13.50),
        ("Grilled Chicken Wrap", "Seasoned chicken, mixed greens, garlic sauce",      11.00),
        ("Margherita Pizza",     "Tomato base, mozzarella, fresh basil",              14.00),
        ("Beef Kofta Platter",   "Spiced kofta with rice, salad, and pita bread",    15.50),
    ]
    for i, (name, desc, price) in enumerate(mains):
        await _upsert_item(db, name=name, description=desc, price=price,
                           category_id=mains_id, restaurant_id=restaurant_id, order=i)

    desserts = [
        ("Chocolate Lava Cake", "Warm cake with a molten chocolate centre",  7.00),
        ("Cheesecake Slice",    "New York-style with berry compote",          6.50),
        ("Kunafa",              "Traditional cheese pastry soaked in syrup",  6.00),
    ]
    for i, (name, desc, price) in enumerate(desserts):
        await _upsert_item(db, name=name, description=desc, price=price,
                           category_id=desserts_id, restaurant_id=restaurant_id, order=i)

    drinks = [
        ("Fresh Lemonade",  "House-squeezed with mint",         4.00),
        ("Mango Juice",     "100% natural, no added sugar",     4.50),
        ("Arabic Coffee",   "Cardamom-spiced qahwa",            3.00),
        ("Soft Drink (can)","Pepsi / 7UP / Mirinda",            2.50),
    ]
    for i, (name, desc, price) in enumerate(drinks):
        await _upsert_item(db, name=name, description=desc, price=price,
                           category_id=drinks_id, restaurant_id=restaurant_id, order=i)

    # ------------------------------------------------------------------
    # Offers
    # ------------------------------------------------------------------
    print("\n► Offers")

    await _upsert_offer(
        db,
        title="Welcome Deal — 10% Off",
        description="Enjoy 10% off your entire order. Valid for a limited time.",
        discount_type="percentage",
        discount_value=10.0,
        restaurant_id=restaurant_id,
        days_from_now=30,
    )
    await _upsert_offer(
        db,
        title="Happy Hour — 5 SAR Off",
        description="5 SAR off any order placed between 3 PM and 6 PM.",
        discount_type="fixed_amount",
        discount_value=5.0,
        restaurant_id=restaurant_id,
        days_from_now=14,
    )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(f"\n{'='*50}")
    print("  Seed complete. Ready to test:\n")
    print(f"  saas_admin  →  {ADMIN_EMAIL}  /  {ADMIN_PASSWORD}")
    print(f"  owner       →  {OWNER_EMAIL}  /  {OWNER_PASSWORD}")
    print(f"  public menu →  /public/menu/{slug}")
    print(f"{'='*50}\n")

    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
