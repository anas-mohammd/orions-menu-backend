from datetime import datetime, timezone, timedelta

import pytest
from httpx import AsyncClient
from motor.motor_asyncio import AsyncIOMotorDatabase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _seed_menu(
    client: AsyncClient,
    headers: dict,
) -> tuple[dict, dict]:
    """Create one category and one item; return (category, item) dicts."""
    cat_resp = await client.post(
        "/menu/categories",
        json={"name": "Mains", "order": 0},
        headers=headers,
    )
    assert cat_resp.status_code == 201, cat_resp.text
    category = cat_resp.json()

    item_resp = await client.post(
        "/menu/items",
        json={
            "name": "Cheeseburger",
            "price": "12.50",
            "category_id": category["id"],
            "order": 0,
        },
        headers=headers,
    )
    assert item_resp.status_code == 201, item_resp.text
    item = item_resp.json()

    return category, item


def _order_url(slug: str) -> str:
    return f"/public/menu/{slug}/order"


# ---------------------------------------------------------------------------
# Successful order
# ---------------------------------------------------------------------------

async def test_place_order_returns_whatsapp_link(
    client: AsyncClient,
    restaurant_owner: dict,
):
    _, item = await _seed_menu(client, restaurant_owner["headers"])
    slug = restaurant_owner["restaurant_slug"]

    response = await client.post(
        _order_url(slug),
        json={
            "customer_name": "Alice",
            "customer_phone": "+966501234567",
            "items": [{"item_id": item["id"], "quantity": 2}],
            "notes": "No onions please",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert "whatsapp_link" in data
    assert data["whatsapp_link"].startswith("https://wa.me/")
    assert "order_id" in data
    assert float(data["total"]) == pytest.approx(25.00)  # 12.50 × 2


async def test_whatsapp_link_contains_encoded_order_details(
    client: AsyncClient,
    restaurant_owner: dict,
):
    """The wa.me link must carry a ?text= query string with URL-encoded content."""
    _, item = await _seed_menu(client, restaurant_owner["headers"])

    response = await client.post(
        _order_url(restaurant_owner["restaurant_slug"]),
        json={
            "customer_name": "Bob",
            "customer_phone": "0501234567",
            "items": [{"item_id": item["id"], "quantity": 1}],
        },
    )

    assert response.status_code == 201
    link = response.json()["whatsapp_link"]
    assert "?text=" in link
    # URL-encoded content should contain the customer name
    assert "Bob" in link or "%42ob" in link or "Bob".replace(" ", "%20") in link


async def test_order_total_is_calculated_server_side(
    client: AsyncClient,
    restaurant_owner: dict,
    test_db: AsyncIOMotorDatabase,
):
    """The total must come from the DB price, not from the request payload."""
    _, item = await _seed_menu(client, restaurant_owner["headers"])
    slug = restaurant_owner["restaurant_slug"]

    response = await client.post(
        _order_url(slug),
        json={
            "customer_name": "Charlie",
            "customer_phone": "0509999999",
            # Client sends no price field — server must derive it from the DB
            "items": [{"item_id": item["id"], "quantity": 3}],
        },
    )

    assert response.status_code == 201
    # 12.50 × 3 = 37.50, regardless of anything the client could send
    assert float(response.json()["total"]) == pytest.approx(37.50)


async def test_order_is_persisted_to_db(
    client: AsyncClient,
    restaurant_owner: dict,
    test_db: AsyncIOMotorDatabase,
):
    _, item = await _seed_menu(client, restaurant_owner["headers"])

    response = await client.post(
        _order_url(restaurant_owner["restaurant_slug"]),
        json={
            "customer_name": "Dana",
            "customer_phone": "0501111111",
            "items": [{"item_id": item["id"], "quantity": 1}],
        },
    )

    assert response.status_code == 201
    order_id = response.json()["order_id"]

    from bson import ObjectId
    doc = await test_db["orders"].find_one({"_id": ObjectId(order_id)})
    assert doc is not None
    assert doc["customer_name"] == "Dana"
    assert doc["whatsapp_link"].startswith("https://wa.me/")


async def test_order_with_multiple_items(
    client: AsyncClient,
    restaurant_owner: dict,
):
    category, item_a = await _seed_menu(client, restaurant_owner["headers"])

    item_b_resp = await client.post(
        "/menu/items",
        json={"name": "Fries", "price": "4.00", "category_id": category["id"], "order": 1},
        headers=restaurant_owner["headers"],
    )
    item_b = item_b_resp.json()

    response = await client.post(
        _order_url(restaurant_owner["restaurant_slug"]),
        json={
            "customer_name": "Eve",
            "customer_phone": "0502222222",
            "items": [
                {"item_id": item_a["id"], "quantity": 1},   # 12.50
                {"item_id": item_b["id"], "quantity": 2},   # 4.00 × 2 = 8.00
            ],
        },
    )

    assert response.status_code == 201
    assert float(response.json()["total"]) == pytest.approx(20.50)


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

async def test_order_empty_items_list_rejected(
    client: AsyncClient,
    restaurant_owner: dict,
):
    response = await client.post(
        _order_url(restaurant_owner["restaurant_slug"]),
        json={
            "customer_name": "Frank",
            "customer_phone": "0503333333",
            "items": [],
        },
    )

    assert response.status_code == 422


async def test_order_zero_quantity_rejected(
    client: AsyncClient,
    restaurant_owner: dict,
):
    _, item = await _seed_menu(client, restaurant_owner["headers"])

    response = await client.post(
        _order_url(restaurant_owner["restaurant_slug"]),
        json={
            "customer_name": "Grace",
            "customer_phone": "0504444444",
            "items": [{"item_id": item["id"], "quantity": 0}],
        },
    )

    assert response.status_code == 422


async def test_order_unknown_item_id_returns_404(
    client: AsyncClient,
    restaurant_owner: dict,
):
    from bson import ObjectId
    fake_id = str(ObjectId())

    response = await client.post(
        _order_url(restaurant_owner["restaurant_slug"]),
        json={
            "customer_name": "Hank",
            "customer_phone": "0505555555",
            "items": [{"item_id": fake_id, "quantity": 1}],
        },
    )

    assert response.status_code == 404


async def test_order_unavailable_item_rejected(
    client: AsyncClient,
    restaurant_owner: dict,
):
    _, item = await _seed_menu(client, restaurant_owner["headers"])

    # Disable the item before ordering
    await client.put(
        f"/menu/items/{item['id']}/toggle",
        headers=restaurant_owner["headers"],
    )

    response = await client.post(
        _order_url(restaurant_owner["restaurant_slug"]),
        json={
            "customer_name": "Ivy",
            "customer_phone": "0506666666",
            "items": [{"item_id": item["id"], "quantity": 1}],
        },
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Restaurant state guards
# ---------------------------------------------------------------------------

async def test_order_unknown_slug_returns_404(client: AsyncClient):
    response = await client.post(
        _order_url("no-such-restaurant"),
        json={
            "customer_name": "Jack",
            "customer_phone": "0507777777",
            "items": [],
        },
    )

    assert response.status_code in (404, 422)


async def test_order_expired_subscription_returns_403(
    client: AsyncClient,
    restaurant_owner: dict,
    test_db: AsyncIOMotorDatabase,
):
    _, item = await _seed_menu(client, restaurant_owner["headers"])
    slug = restaurant_owner["restaurant_slug"]

    await test_db["restaurants"].update_one(
        {"slug": slug},
        {"$set": {
            "subscription_status": "expired",
            "subscription_expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc),
        }},
    )

    response = await client.post(
        _order_url(slug),
        json={
            "customer_name": "Karen",
            "customer_phone": "0508888888",
            "items": [{"item_id": item["id"], "quantity": 1}],
        },
    )

    assert response.status_code == 403


async def test_order_inactive_restaurant_returns_404(
    client: AsyncClient,
    restaurant_owner: dict,
    test_db: AsyncIOMotorDatabase,
):
    _, item = await _seed_menu(client, restaurant_owner["headers"])
    slug = restaurant_owner["restaurant_slug"]

    await test_db["restaurants"].update_one(
        {"slug": slug},
        {"$set": {"is_active": False}},
    )

    response = await client.post(
        _order_url(slug),
        json={
            "customer_name": "Leo",
            "customer_phone": "0509000000",
            "items": [{"item_id": item["id"], "quantity": 1}],
        },
    )

    assert response.status_code == 404
