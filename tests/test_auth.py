import pytest
from httpx import AsyncClient


REGISTER_URL = "/auth/register"
LOGIN_URL = "/auth/login"
REFRESH_URL = "/auth/refresh"
ME_URL = "/auth/me"

VALID_USER = {
    "name": "Jane Doe",
    "email": "jane@example.com",
    "password": "securepass123",
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def test_register_success(client: AsyncClient):
    response = await client.post(REGISTER_URL, json=VALID_USER)

    assert response.status_code == 201
    data = response.json()
    assert data["email"] == VALID_USER["email"]
    assert data["name"] == VALID_USER["name"]
    assert data["role"] == "restaurant_owner"
    assert "id" in data
    assert "hashed_password" not in data
    assert "password" not in data


async def test_register_duplicate_email(client: AsyncClient):
    await client.post(REGISTER_URL, json=VALID_USER)
    response = await client.post(REGISTER_URL, json=VALID_USER)

    assert response.status_code == 409
    assert "already registered" in response.json()["detail"].lower()


async def test_register_saas_admin_role_is_forbidden(client: AsyncClient):
    payload = {**VALID_USER, "role": "saas_admin"}
    response = await client.post(REGISTER_URL, json=payload)

    assert response.status_code == 403


async def test_register_short_password_rejected(client: AsyncClient):
    payload = {**VALID_USER, "password": "short"}
    response = await client.post(REGISTER_URL, json=payload)

    assert response.status_code == 422


async def test_register_invalid_email_rejected(client: AsyncClient):
    payload = {**VALID_USER, "email": "not-an-email"}
    response = await client.post(REGISTER_URL, json=payload)

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def test_login_success(client: AsyncClient):
    await client.post(REGISTER_URL, json=VALID_USER)

    response = await client.post(LOGIN_URL, json={
        "email": VALID_USER["email"],
        "password": VALID_USER["password"],
    })

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


async def test_login_wrong_password(client: AsyncClient):
    await client.post(REGISTER_URL, json=VALID_USER)

    response = await client.post(LOGIN_URL, json={
        "email": VALID_USER["email"],
        "password": "wrongpassword",
    })

    assert response.status_code == 401
    assert "incorrect" in response.json()["detail"].lower()


async def test_login_unknown_email(client: AsyncClient):
    response = await client.post(LOGIN_URL, json={
        "email": "nobody@example.com",
        "password": "doesnotmatter",
    })

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

async def test_refresh_token_returns_new_access_token(client: AsyncClient):
    await client.post(REGISTER_URL, json=VALID_USER)
    login_resp = await client.post(LOGIN_URL, json={
        "email": VALID_USER["email"],
        "password": VALID_USER["password"],
    })
    refresh_token = login_resp.json()["refresh_token"]

    response = await client.post(REFRESH_URL, json={"refresh_token": refresh_token})

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert "refresh_token" not in data


async def test_refresh_with_invalid_token_rejected(client: AsyncClient):
    response = await client.post(REFRESH_URL, json={"refresh_token": "this.is.invalid"})

    assert response.status_code == 401


async def test_refresh_with_access_token_rejected(client: AsyncClient):
    """An access token must not be accepted in place of a refresh token."""
    await client.post(REGISTER_URL, json=VALID_USER)
    login_resp = await client.post(LOGIN_URL, json={
        "email": VALID_USER["email"],
        "password": VALID_USER["password"],
    })
    access_token = login_resp.json()["access_token"]

    response = await client.post(REFRESH_URL, json={"refresh_token": access_token})

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------

async def test_me_returns_current_user(client: AsyncClient):
    await client.post(REGISTER_URL, json=VALID_USER)
    login_resp = await client.post(LOGIN_URL, json={
        "email": VALID_USER["email"],
        "password": VALID_USER["password"],
    })
    access_token = login_resp.json()["access_token"]

    response = await client.get(ME_URL, headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == VALID_USER["email"]
    assert data["name"] == VALID_USER["name"]
    assert "hashed_password" not in data


async def test_me_without_token_rejected(client: AsyncClient):
    response = await client.get(ME_URL)

    assert response.status_code == 403


async def test_me_with_invalid_token_rejected(client: AsyncClient):
    response = await client.get(ME_URL, headers={"Authorization": "Bearer invalid.token.here"})

    assert response.status_code == 401


async def test_me_uses_saas_admin_fixture(client: AsyncClient, saas_admin: dict):
    """Verify the saas_admin conftest fixture returns usable auth headers."""
    response = await client.get(ME_URL, headers=saas_admin["headers"])

    assert response.status_code == 200
    assert response.json()["role"] == "saas_admin"


async def test_me_uses_restaurant_owner_fixture(client: AsyncClient, restaurant_owner: dict):
    """Verify the restaurant_owner conftest fixture returns usable auth headers."""
    response = await client.get(ME_URL, headers=restaurant_owner["headers"])

    assert response.status_code == 200
    assert response.json()["role"] == "restaurant_owner"
