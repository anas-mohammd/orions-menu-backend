from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.core.config import settings

# Module-level client and db references
_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def connect_db() -> None:
    global _client, _db
    _client = AsyncIOMotorClient(
        settings.mongodb_url,
        maxPoolSize=10,
        minPoolSize=1,
        serverSelectionTimeoutMS=5000,
    )
    _db = _client[settings.mongodb_db_name]
    await _create_indexes(_db)


async def _create_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create all required indexes on startup. Safe to call multiple times (idempotent)."""
    from pymongo import ASCENDING, IndexModel

    await db["users"].create_indexes([
        IndexModel([("email", ASCENDING)], unique=True, name="email_unique"),
    ])
    await db["restaurants"].create_indexes([
        IndexModel([("slug", ASCENDING)], unique=True, name="slug_unique"),
        IndexModel([("owner_ids", ASCENDING)], name="owner_ids"),
    ])
    await db["categories"].create_indexes([
        IndexModel([("restaurant_id", ASCENDING), ("is_active", ASCENDING)], name="restaurant_active_categories"),
    ])
    await db["menu_items"].create_indexes([
        IndexModel([("restaurant_id", ASCENDING), ("is_available", ASCENDING)], name="restaurant_available_items"),
    ])
    await db["offers"].create_indexes([
        IndexModel([("restaurant_id", ASCENDING), ("is_active", ASCENDING)], name="restaurant_active_offers"),
    ])
    await db["orders"].create_indexes([
        IndexModel([("restaurant_id", ASCENDING), ("created_at", ASCENDING)], name="restaurant_orders"),
    ])
    await db["reviews"].create_indexes([
        IndexModel([("restaurant_id", ASCENDING), ("created_at", ASCENDING)], name="restaurant_reviews"),
    ])


async def disconnect_db() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database not connected. Call connect_db() first.")
    return _db
