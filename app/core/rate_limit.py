from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis

from app.core.dependencies import get_redis


def rate_limit(max_requests: int, window_seconds: int, key_prefix: str):
    """Return a FastAPI dependency that enforces a fixed-window rate limit.

    Args:
        max_requests:    Maximum number of requests allowed per window.
        window_seconds:  Length of the window in seconds.
        key_prefix:      Short label used in the Redis key (e.g. "login", "order").

    Usage:
        @router.post("/login")
        async def login(..., _: None = Depends(rate_limit(5, 60, "login"))):
            ...

    Raises:
        HTTP 429 with a Retry-After header when the limit is exceeded.
    """

    async def dependency(
        request: Request,
        redis: Redis = Depends(get_redis),
    ) -> None:
        # Prefer the real client IP when behind a reverse proxy
        forwarded_for = request.headers.get("X-Forwarded-For")
        ip = forwarded_for.split(",")[0].strip() if forwarded_for else (
            request.client.host if request.client else "unknown"
        )

        key = f"rate_limit:{key_prefix}:{ip}"

        # Increment counter; set TTL on the first request in the window
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window_seconds)

        if count > max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many requests. Maximum {max_requests} per {window_seconds} seconds.",
                headers={"Retry-After": str(window_seconds)},
            )

    return dependency
