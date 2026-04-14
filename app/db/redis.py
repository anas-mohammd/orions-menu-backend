from redis.asyncio import Redis, ConnectionPool
from app.core.config import settings

# Module-level pool and client references
_pool: ConnectionPool | None = None
_redis: Redis | None = None


async def connect_redis() -> None:
    global _pool, _redis

    # If REDIS_URL starts with "fakeredis://", use in-memory fakeredis (no server needed)
    if settings.redis_url.startswith("fakeredis://"):
        import fakeredis.aioredis as fakeredis
        _redis = fakeredis.FakeRedis(decode_responses=True)
        print("Redis   : using fakeredis (in-memory, no server required)")
        return

    _pool = ConnectionPool.from_url(
        settings.redis_url,
        max_connections=10,
        decode_responses=True,
    )
    _redis = Redis(connection_pool=_pool)


async def disconnect_redis() -> None:
    global _pool, _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
    if _pool is not None:
        await _pool.aclose()
        _pool = None


def get_redis() -> Redis:
    if _redis is None:
        raise RuntimeError("Redis not connected. Call connect_redis() first.")
    return _redis


async def invalidate_menu_cache(slug: str, redis: Redis) -> None:
    """Delete the cached public menu entry for the given restaurant slug."""
    await redis.delete(f"menu:{slug}")
