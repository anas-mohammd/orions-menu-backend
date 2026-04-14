from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis

from app.core.security import decode_token
from app.db.mongodb import get_db as _get_db
from app.db.redis import get_redis as _get_redis
from app.models.user import UserResponse, UserRole, user_from_doc

bearer_scheme = HTTPBearer()


# ---------------------------------------------------------------------------
# Database dependencies
# ---------------------------------------------------------------------------

async def get_db() -> AsyncIOMotorDatabase:
    return _get_db()


async def get_redis() -> Redis:
    return _get_redis()


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> UserResponse:
    """Decode the Bearer JWT and return the authenticated user.

    Raises 401 if the token is missing, invalid, or the user no longer exists.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(credentials.credentials)
        user_id: str | None = payload.get("sub")
        token_type: str | None = payload.get("type")
        if user_id is None or token_type != "access":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    from bson import ObjectId

    doc = await db["users"].find_one({"_id": ObjectId(user_id)})
    if doc is None:
        raise credentials_exception

    return UserResponse(**user_from_doc(doc))


# ---------------------------------------------------------------------------
# Role guards
# ---------------------------------------------------------------------------

async def require_saas_admin(
    current_user: UserResponse = Depends(get_current_user),
) -> UserResponse:
    """Allow only saas_admin users through. Raises 403 otherwise."""
    if current_user.role != UserRole.saas_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SaaS admin access required",
        )
    return current_user


async def require_restaurant_owner(
    current_user: UserResponse = Depends(get_current_user),
) -> UserResponse:
    """Allow only restaurant_owner users through. Raises 403 otherwise."""
    if current_user.role != UserRole.restaurant_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Restaurant owner access required",
        )
    return current_user


# ---------------------------------------------------------------------------
# Restaurant ownership guard
# ---------------------------------------------------------------------------

async def get_restaurant_or_403(
    restaurant_id: str,
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    """Return the restaurant document if the current owner owns it.

    Raises 404 if the restaurant does not exist.
    Raises 403 if the current user is not the owner.
    """
    from bson import ObjectId

    try:
        oid = ObjectId(restaurant_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant not found",
        )

    doc = await db["restaurants"].find_one({"_id": oid})
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant not found",
        )

    if current_user.id not in [str(oid) for oid in doc.get("owner_ids", [])]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this restaurant",
        )

    from app.models.restaurant import restaurant_from_doc
    return restaurant_from_doc(doc)
