from datetime import datetime, timezone, timedelta

import pytest
from httpx import AsyncClient
from motor.motor_asyncio import AsyncIOMotorDatabase

CATEGORIES_URL = "/menu/categories"
ITEMS_URL = "/menu/items"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_category(client: AsyncClient, headers: dict, **overrides) -> dict:
    payload = {"name": "Starters", "order": 0, **overrides}
    response = await client.post(CATEGORIES_URL, json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _create_item(
    client: AsyncClient,
    headers: dict,
    category_id: str,
    **overrides,
) -> dict:
    payload = {
        "name": "Margherita",
        "price": "12.50",
        "category_id": category_id,
        "order": 0,
        **overrides,
    }
    response = await client.post(ITEMS_URL, json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _make_second_owner(test_db: AsyncIOMotorDatabase) -> dict:
    """Insert a second restaurant owner + restaurant directly into the test DB."""
    from bson import ObjectId
    from app.core.security import hash_password, create_access_token
    from app.models.user import UserRole

    now = datetime.now(timezone.utc)
    user_doc = {
        "name": "Other Owner",
        "email": "other@example.com",
        "hashed_password": hash_password("otherpassword123"),
        "role": UserRole.restaurant_owner.value,
        "created_at": now,
    }
    user_result = await test_db["users"].insert_one(user_doc)
    user_id = str(user_result.inserted_id)

    restaurant_doc = {
        "name": "Other Restaurant",
        "slug": "other-restaurant",
        "description": None,
        "logo_url": None,
        "whatsapp_number": "0987654321",
        "owner_ids": [user_id],
        "subscription_start_date": now,
        "subscription_expires_at": now + timedelta(days=365),
        "subscription_status": "active",
        "is_active": True,
        "created_at": now,
    }
    restaurant_result = await test_db["restaurants"].insert_one(restaurant_doc)

    token = create_access_token(
        subject=user_id,
        extra={"role": UserRole.restaurant_owner.value},
    )
    return {
        "id": user_id,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}"},
        "restaurant_id": str(restaurant_result.inserted_id),
        "restaurant_slug": "other-restaurant",
    }


# ---------------------------------------------------------------------------
# Category tests
# ---------------------------------------------------------------------------

async def test_create_category_success(client: AsyncClient, restaurant_owner: dict):
    response = await client.post(
        CATEGORIES_URL,
        json={"name": "Desserts", "order": 1},
        headers=restaurant_owner["headers"],
    )

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Desserts"
    assert data["order"] == 1
    assert data["is_active"] is True
    assert data["restaurant_id"] == restaurant_owner["restaurant_id"]
    assert "id" in data


async def test_create_category_requires_auth(client: AsyncClient):
    response = await client.post(CATEGORIES_URL, json={"name": "Drinks", "order": 0})

    assert response.status_code == 403


async def test_create_category_name_too_short(client: AsyncClient, restaurant_owner: dict):
    response = await client.post(
        CATEGORIES_URL,
        json={"name": "A", "order": 0},
        headers=restaurant_owner["headers"],
    )

    assert response.status_code == 422


async def test_list_categories_sorted_by_order(client: AsyncClient, restaurant_owner: dict):
    await _create_category(client, restaurant_owner["headers"], name="Mains", order=2)
    await _create_category(client, restaurant_owner["headers"], name="Starters", order=0)
    await _create_category(client, restaurant_owner["headers"], name="Drinks", order=1)

    response = await client.get(CATEGORIES_URL, headers=restaurant_owner["headers"])

    assert response.status_code == 200
    names = [c["name"] for c in response.json()]
    assert names == ["Starters", "Drinks", "Mains"]


async def test_update_category(client: AsyncClient, restaurant_owner: dict):
    category = await _create_category(client, restaurant_owner["headers"])

    response = await client.put(
        f"{CATEGORIES_URL}/{category['id']}",
        json={"name": "Updated Name"},
        headers=restaurant_owner["headers"],
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Updated Name"


async def test_delete_category_success(client: AsyncClient, restaurant_owner: dict):
    category = await _create_category(client, restaurant_owner["headers"])

    response = await client.delete(
        f"{CATEGORIES_URL}/{category['id']}",
        headers=restaurant_owner["headers"],
    )

    assert response.status_code == 204


async def test_delete_category_with_items_rejected(
    client: AsyncClient,
    restaurant_owner: dict,
):
    category = await _create_category(client, restaurant_owner["headers"])
    await _create_item(client, restaurant_owner["headers"], category["id"])

    response = await client.delete(
        f"{CATEGORIES_URL}/{category['id']}",
        headers=restaurant_owner["headers"],
    )

    assert response.status_code == 409
    assert "item" in response.json()["detail"].lower()


async def test_reorder_categories(client: AsyncClient, restaurant_owner: dict):
    cat_a = await _create_category(client, restaurant_owner["headers"], name="A", order=0)
    cat_b = await _create_category(client, restaurant_owner["headers"], name="B", order=1)

    response = await client.put(
        f"{CATEGORIES_URL}/reorder",
        json={"categories": [
            {"id": cat_a["id"], "order": 5},
            {"id": cat_b["id"], "order": 3},
        ]},
        headers=restaurant_owner["headers"],
    )

    assert response.status_code == 204

    listed = await client.get(CATEGORIES_URL, headers=restaurant_owner["headers"])
    orders = {c["name"]: c["order"] for c in listed.json()}
    assert orders["A"] == 5
    assert orders["B"] == 3


# ---------------------------------------------------------------------------
# Menu item tests
# ---------------------------------------------------------------------------

async def test_create_item_success(client: AsyncClient, restaurant_owner: dict):
    category = await _create_category(client, restaurant_owner["headers"])

    response = await client.post(
        ITEMS_URL,
        json={
            "name": "Caesar Salad",
            "price": "9.99",
            "category_id": category["id"],
            "order": 0,
        },
        headers=restaurant_owner["headers"],
    )

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Caesar Salad"
    assert float(data["price"]) == pytest.approx(9.99)
    assert data["is_available"] is True
    assert data["restaurant_id"] == restaurant_owner["restaurant_id"]
    assert data["category_id"] == category["id"]


async def test_create_item_requires_auth(client: AsyncClient, restaurant_owner: dict):
    category = await _create_category(client, restaurant_owner["headers"])

    response = await client.post(
        ITEMS_URL,
        json={"name": "Burger", "price": "8.00", "category_id": category["id"], "order": 0},
    )

    assert response.status_code == 403


async def test_create_item_zero_price_rejected(client: AsyncClient, restaurant_owner: dict):
    category = await _create_category(client, restaurant_owner["headers"])

    response = await client.post(
        ITEMS_URL,
        json={"name": "Free Item", "price": "0.00", "category_id": category["id"], "order": 0},
        headers=restaurant_owner["headers"],
    )

    assert response.status_code == 422


async def test_update_item(client: AsyncClient, restaurant_owner: dict):
    category = await _create_category(client, restaurant_owner["headers"])
    item = await _create_item(client, restaurant_owner["headers"], category["id"])

    response = await client.put(
        f"{ITEMS_URL}/{item['id']}",
        json={"price": "15.00", "name": "Updated Margherita"},
        headers=restaurant_owner["headers"],
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Margherita"
    assert float(data["price"]) == pytest.approx(15.00)


async def test_toggle_item_availability(client: AsyncClient, restaurant_owner: dict):
    category = await _create_category(client, restaurant_owner["headers"])
    item = await _create_item(client, restaurant_owner["headers"], category["id"])
    assert item["is_available"] is True

    response = await client.put(
        f"{ITEMS_URL}/{item['id']}/toggle",
        headers=restaurant_owner["headers"],
    )

    assert response.status_code == 200
    assert response.json()["is_available"] is False

    # Toggle back on
    response = await client.put(
        f"{ITEMS_URL}/{item['id']}/toggle",
        headers=restaurant_owner["headers"],
    )
    assert response.json()["is_available"] is True


async def test_delete_item(client: AsyncClient, restaurant_owner: dict):
    category = await _create_category(client, restaurant_owner["headers"])
    item = await _create_item(client, restaurant_owner["headers"], category["id"])

    response = await client.delete(
        f"{ITEMS_URL}/{item['id']}",
        headers=restaurant_owner["headers"],
    )

    assert response.status_code == 204


async def test_list_items_filtered_by_category(client: AsyncClient, restaurant_owner: dict):
    cat_a = await _create_category(client, restaurant_owner["headers"], name="Cat A", order=0)
    cat_b = await _create_category(client, restaurant_owner["headers"], name="Cat B", order=1)
    await _create_item(client, restaurant_owner["headers"], cat_a["id"], name="Item A1")
    await _create_item(client, restaurant_owner["headers"], cat_a["id"], name="Item A2")
    await _create_item(client, restaurant_owner["headers"], cat_b["id"], name="Item B1")

    response = await client.get(
        ITEMS_URL,
        params={"category_id": cat_a["id"]},
        headers=restaurant_owner["headers"],
    )

    assert response.status_code == 200
    names = {item["name"] for item in response.json()}
    assert names == {"Item A1", "Item A2"}


# ---------------------------------------------------------------------------
# Multi-tenancy isolation
# ---------------------------------------------------------------------------

async def test_owner_cannot_modify_another_owners_category(
    client: AsyncClient,
    restaurant_owner: dict,
    test_db: AsyncIOMotorDatabase,
):
    """An owner who creates a category should not be able to update it using
    another owner's token."""
    category = await _create_category(client, restaurant_owner["headers"])

    other = await _make_second_owner(test_db)

    response = await client.put(
        f"{CATEGORIES_URL}/{category['id']}",
        json={"name": "Hijacked"},
        headers=other["headers"],
    )

    # 404 because the category doesn't exist under the other owner's restaurant
    assert response.status_code == 404


async def test_owner_cannot_delete_another_owners_item(
    client: AsyncClient,
    restaurant_owner: dict,
    test_db: AsyncIOMotorDatabase,
):
    category = await _create_category(client, restaurant_owner["headers"])
    item = await _create_item(client, restaurant_owner["headers"], category["id"])

    other = await _make_second_owner(test_db)

    response = await client.delete(
        f"{ITEMS_URL}/{item['id']}",
        headers=other["headers"],
    )

    assert response.status_code == 404


async def test_owner_cannot_add_item_to_another_owners_category(
    client: AsyncClient,
    restaurant_owner: dict,
    test_db: AsyncIOMotorDatabase,
):
    category = await _create_category(client, restaurant_owner["headers"])

    other = await _make_second_owner(test_db)

    response = await client.post(
        ITEMS_URL,
        json={"name": "Hijacked Item", "price": "5.00", "category_id": category["id"], "order": 0},
        headers=other["headers"],
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Public menu endpoint
# ---------------------------------------------------------------------------

async def test_public_menu_returns_full_data(
    client: AsyncClient,
    restaurant_owner: dict,
    test_db: AsyncIOMotorDatabase,
):
    category = await _create_category(client, restaurant_owner["headers"], name="Burgers")
    await _create_item(client, restaurant_owner["headers"], category["id"], name="Classic Burger", price="11.00")

    slug = restaurant_owner["restaurant_slug"]
    response = await client.get(f"/public/menu/{slug}")

    assert response.status_code == 200
    data = response.json()
    assert data["restaurant"]["slug"] == slug
    assert any(c["name"] == "Burgers" for c in data["categories"])
    assert any(i["name"] == "Classic Burger" for i in data["items"])


async def test_public_menu_excludes_unavailable_items(
    client: AsyncClient,
    restaurant_owner: dict,
):
    category = await _create_category(client, restaurant_owner["headers"])
    item = await _create_item(client, restaurant_owner["headers"], category["id"], name="Hidden Item")

    # Disable the item
    await client.put(
        f"{ITEMS_URL}/{item['id']}/toggle",
        headers=restaurant_owner["headers"],
    )

    slug = restaurant_owner["restaurant_slug"]
    response = await client.get(f"/public/menu/{slug}")

    assert response.status_code == 200
    item_names = [i["name"] for i in response.json()["items"]]
    assert "Hidden Item" not in item_names


async def test_public_menu_excludes_inactive_categories(
    client: AsyncClient,
    restaurant_owner: dict,
):
    await _create_category(client, restaurant_owner["headers"], name="Visible Category")
    hidden = await _create_category(client, restaurant_owner["headers"], name="Hidden Category")

    # Deactivate the category
    await client.put(
        f"{CATEGORIES_URL}/{hidden['id']}",
        json={"is_active": False},
        headers=restaurant_owner["headers"],
    )

    slug = restaurant_owner["restaurant_slug"]
    response = await client.get(f"/public/menu/{slug}")

    assert response.status_code == 200
    category_names = [c["name"] for c in response.json()["categories"]]
    assert "Hidden Category" not in category_names
    assert "Visible Category" in category_names


async def test_public_menu_unknown_slug_returns_404(client: AsyncClient):
    response = await client.get("/public/menu/does-not-exist")

    assert response.status_code == 404


async def test_public_menu_expired_subscription_returns_403(
    client: AsyncClient,
    restaurant_owner: dict,
    test_db: AsyncIOMotorDatabase,
):
    # Manually expire the subscription in the test DB
    await test_db["restaurants"].update_one(
        {"slug": restaurant_owner["restaurant_slug"]},
        {"$set": {
            "subscription_status": "expired",
            "subscription_expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc),
        }},
    )

    response = await client.get(f"/public/menu/{restaurant_owner['restaurant_slug']}")

    assert response.status_code == 403


async def test_public_menu_served_from_cache_on_second_request(
    client: AsyncClient,
    restaurant_owner: dict,
    test_db: AsyncIOMotorDatabase,
):
    slug = restaurant_owner["restaurant_slug"]
    await _create_category(client, restaurant_owner["headers"], name="Cache Test Category")

    # First request populates the cache
    first = await client.get(f"/public/menu/{slug}")
    assert first.status_code == 200

    # Modify the DB directly — a cached response will NOT reflect this
    await test_db["restaurants"].update_one(
        {"slug": slug},
        {"$set": {"name": "Name Changed After Cache"}},
    )

    # Second request should still return the cached (original) restaurant name
    second = await client.get(f"/public/menu/{slug}")
    assert second.status_code == 200
    assert second.json()["restaurant"]["name"] != "Name Changed After Cache"
