from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from bson import ObjectId
from httpx import AsyncClient, ASGITransport
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from redis.asyncio import ConnectionPool, Redis

from app.core.security import create_access_token, hash_password
from app.db.mongodb import get_db
from app.db.redis import get_redis
from app.main import app
from app.models.user import UserRole

# ---------------------------------------------------------------------------
# Test infrastructure config
# ---------------------------------------------------------------------------

_MONGODB_URL = "mongodb://localhost:27017"
_TEST_DB_NAME = "saas_menu_test"
_REDIS_URL = "redis://localhost:6379"
_TEST_REDIS_DB = 1  # Isolate test data from the default DB (index 0)


# ---------------------------------------------------------------------------
# MongoDB fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def mongo_client() -> AsyncIOMotorClient:
    """Single Motor client shared across the entire test session.

    Motor client creation is synchronous, so this does not need to be async.
    """
    client = AsyncIOMotorClient(_MONGODB_URL)
    yield client
    client.close()


@pytest_asyncio.fixture
async def test_db(mongo_client: AsyncIOMotorClient) -> AsyncIOMotorDatabase:
    """Fresh test database for each test.

    All collections are dropped after the test completes, guaranteeing isolation.
    """
    db = mongo_client[_TEST_DB_NAME]
    yield db
    for name in await db.list_collection_names():
        await db[name].drop()


# ---------------------------------------------------------------------------
# Redis fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def test_redis() -> Redis:
    """Redis client on DB index 1, flushed after each test."""
    pool = ConnectionPool.from_url(
        f"{_REDIS_URL}/{_TEST_REDIS_DB}",
        max_connections=5,
        decode_responses=True,
    )
    redis = Redis(connection_pool=pool)
    yield redis
    await redis.flushdb()
    await redis.aclose()
    await pool.aclose()


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(test_db: AsyncIOMotorDatabase, test_redis: Redis) -> AsyncClient:
    """Async test client with dependency overrides pointing at the test DB and Redis."""
    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[get_redis] = lambda: test_redis

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# User / auth fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def saas_admin(test_db: AsyncIOMotorDatabase) -> dict:
    """A saas_admin user already inserted in the test DB, with a valid access token.

    Returns a dict with keys: id, email, password, token, headers.
    """
    password = "adminpassword123"
    doc = {
        "name": "Test Admin",
        "email": "admin@test.com",
        "hashed_password": hash_password(password),
        "role": UserRole.saas_admin.value,
        "created_at": datetime.now(timezone.utc),
    }
    result = await test_db["users"].insert_one(doc)
    user_id = str(result.inserted_id)

    token = create_access_token(
        subject=user_id,
        extra={"role": UserRole.saas_admin.value},
    )
    return {
        "id": user_id,
        "email": doc["email"],
        "password": password,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
    }


@pytest_asyncio.fixture
async def restaurant_owner(test_db: AsyncIOMotorDatabase) -> dict:
    """A restaurant_owner user with a linked active restaurant.

    Returns a dict with keys:
        id, email, password, token, headers,
        restaurant_id, restaurant_slug.
    """
    password = "ownerpassword123"
    now = datetime.now(timezone.utc)

    # Insert owner user
    user_doc = {
        "name": "Test Owner",
        "email": "owner@test.com",
        "hashed_password": hash_password(password),
        "role": UserRole.restaurant_owner.value,
        "created_at": now,
    }
    user_result = await test_db["users"].insert_one(user_doc)
    user_id = str(user_result.inserted_id)

    # Insert linked restaurant with an active subscription
    slug = "test-restaurant"
    restaurant_doc = {
        "name": "Test Restaurant",
        "slug": slug,
        "description": "A test restaurant",
        "logo_url": None,
        "whatsapp_number": "1234567890",
        "owner_ids": [user_id],
        "subscription_start_date": now,
        "subscription_expires_at": now + timedelta(days=365),
        "subscription_status": "active",
        "is_active": True,
        "created_at": now,
    }
    restaurant_result = await test_db["restaurants"].insert_one(restaurant_doc)
    restaurant_id = str(restaurant_result.inserted_id)

    token = create_access_token(
        subject=user_id,
        extra={"role": UserRole.restaurant_owner.value},
    )
    return {
        "id": user_id,
        "email": user_doc["email"],
        "password": password,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
        "restaurant_id": restaurant_id,
        "restaurant_slug": slug,
    }
