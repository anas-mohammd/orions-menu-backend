"""
Seed script — creates only the saas_admin account.

Run from inside the container:
    docker compose exec api python scripts/seed.py

Credentials are read from environment variables:
    SEED_ADMIN_EMAIL     (default: admin@orionmenu.com)
    SEED_ADMIN_PASSWORD  (default: Admin123!)
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env if running outside Docker
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.security import hash_password
from app.models.user import UserRole

# ── Config ────────────────────────────────────────────────────────────────────
MONGODB_URL    = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DB_NAME        = os.getenv("MONGODB_DB_NAME", "saas_menu")
ADMIN_EMAIL    = os.getenv("SEED_ADMIN_EMAIL", "admin@orionmenu.com")
ADMIN_PASSWORD = os.getenv("SEED_ADMIN_PASSWORD", "Admin123!")


async def seed() -> None:
    client = AsyncIOMotorClient(MONGODB_URL)
    db = client[DB_NAME]

    print(f"\n{'='*50}")
    print(f"  Seeding database: {DB_NAME}")
    print(f"{'='*50}\n")

    existing = await db["users"].find_one({"email": ADMIN_EMAIL})
    if existing:
        print(f"  [skip] admin already exists: {ADMIN_EMAIL}")
    else:
        await db["users"].insert_one({
            "name": "Super Admin",
            "email": ADMIN_EMAIL,
            "hashed_password": hash_password(ADMIN_PASSWORD),
            "role": UserRole.saas_admin.value,
            "created_at": datetime.now(timezone.utc),
        })
        print(f"  [ok]   created saas_admin: {ADMIN_EMAIL}")

    print(f"\n{'='*50}")
    print(f"  Done! Login with:")
    print(f"  Email:    {ADMIN_EMAIL}")
    print(f"  Password: {ADMIN_PASSWORD}")
    print(f"{'='*50}\n")

    client.close()


if __name__ == "__main__":
    asyncio.run(seed())
