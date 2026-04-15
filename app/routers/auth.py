from datetime import timedelta

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from redis.asyncio import Redis

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db, get_redis
from app.core.rate_limit import rate_limit
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import UserCreate, UserResponse, UserRole, user_from_doc

router = APIRouter()

_REFRESH_KEY_PREFIX = "refresh_token:"

_401 = {401: {"description": "Invalid or missing credentials"}}
_403 = {403: {"description": "Action not permitted for this role"}}
_409 = {409: {"description": "Email already registered"}}


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    model_config = ConfigDict(json_schema_extra={
        "example": {"email": "ahmed@restaurant.com", "password": "securepass123"}
    })


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
            "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
            "token_type": "bearer",
        }
    })


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
            "token_type": "bearer",
        }
    })


class RefreshRequest(BaseModel):
    refresh_token: str

    model_config = ConfigDict(json_schema_extra={
        "example": {"refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _store_refresh_token(redis: Redis, token: str, user_id: str) -> None:
    ttl = timedelta(days=settings.refresh_token_expire_days)
    await redis.setex(
        f"{_REFRESH_KEY_PREFIX}{token}",
        int(ttl.total_seconds()),
        user_id,
    )


async def _revoke_refresh_token(redis: Redis, token: str) -> None:
    await redis.delete(f"{_REFRESH_KEY_PREFIX}{token}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new restaurant owner",
    description=(
        "Creates a new `restaurant_owner` account. "
        "Attempting to register with `role: saas_admin` is rejected with **403**. "
        "The `saas_admin` account must be created via the seed script."
    ),
    responses={**_409, **_403},
)
async def register(
    payload: UserCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> UserResponse:
    if payload.role == UserRole.saas_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="لا يمكن التسجيل كمدير النظام",
        )

    existing = await db["users"].find_one({"email": payload.email})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="البريد الإلكتروني مسجل مسبقاً",
        )

    from datetime import datetime, timezone

    doc = {
        "name": payload.name,
        "email": payload.email,
        "hashed_password": hash_password(payload.password),
        "role": UserRole.restaurant_owner.value,
        "created_at": datetime.now(timezone.utc),
    }
    result = await db["users"].insert_one(doc)
    doc["_id"] = result.inserted_id

    return UserResponse(**user_from_doc(doc))


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and receive tokens",
    description=(
        "Authenticate with email and password. "
        "Returns a short-lived **access token** (used as Bearer) and a long-lived **refresh token** "
        "stored in Redis. "
        "Rate limited to **5 attempts per minute** per IP."
    ),
    responses=_401,
)
async def login(
    payload: LoginRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
    _: None = Depends(rate_limit(max_requests=5, window_seconds=60, key_prefix="login")),
) -> TokenResponse:
    doc = await db["users"].find_one({"email": payload.email})
    if not doc or not verify_password(payload.password, doc["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="البريد الإلكتروني أو كلمة المرور غير صحيحة",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = str(doc["_id"])
    access_token = create_access_token(subject=user_id, extra={"role": doc["role"]})
    refresh_token = create_refresh_token(subject=user_id)
    await _store_refresh_token(redis, refresh_token, user_id)

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    summary="Renew access token",
    description=(
        "Exchange a valid **refresh token** for a new **access token**. "
        "The refresh token must still exist in Redis (not expired or revoked). "
        "Submitting an access token here is rejected."
    ),
    responses=_401,
)
async def refresh(
    payload: RefreshRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> AccessTokenResponse:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="رمز التحديث غير صالح أو منتهي الصلاحية",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        token_data = decode_token(payload.refresh_token)
        if token_data.get("type") != "refresh":
            raise credentials_exception
        user_id: str = token_data["sub"]
    except JWTError:
        raise credentials_exception

    stored_user_id = await redis.get(f"{_REFRESH_KEY_PREFIX}{payload.refresh_token}")
    if stored_user_id is None or stored_user_id != user_id:
        raise credentials_exception

    doc = await db["users"].find_one({"_id": ObjectId(user_id)})
    if doc is None:
        raise credentials_exception

    access_token = create_access_token(subject=user_id, extra={"role": doc["role"]})
    return AccessTokenResponse(access_token=access_token)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
    description="Returns the profile of the currently authenticated user.",
    responses=_401,
)
async def me(
    current_user: UserResponse = Depends(get_current_user),
) -> UserResponse:
    return current_user
