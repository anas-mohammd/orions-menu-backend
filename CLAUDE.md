# CLAUDE.md — SaaS Digital Menu (Backend)

## Project Overview
A SaaS platform that allows restaurant owners to create a digital menu.
Customers browse the menu and send their order via WhatsApp.

## Project Structure
```
backend/
├── app/
│   ├── main.py                  # Entry point - FastAPI app
│   ├── core/
│   │   ├── config.py            # Environment variables settings
│   │   ├── security.py          # JWT, hashing
│   │   └── dependencies.py      # Shared dependency injection
│   ├── db/
│   │   ├── mongodb.py           # MongoDB connection (Motor async)
│   │   └── redis.py             # Redis connection
│   ├── models/                  # Pydantic models + MongoDB schemas
│   │   ├── user.py              # SaaS admin + restaurant owner
│   │   ├── restaurant.py        # Restaurant data
│   │   ├── menu.py              # Items and categories
│   │   ├── offer.py             # Offers
│   │   └── order.py             # Orders
│   ├── routers/                 # API endpoints
│   │   ├── auth.py              # Login and registration
│   │   ├── admin.py             # SaaS owner dashboard
│   │   ├── restaurants.py       # Restaurant management
│   │   ├── menu.py              # Menu management
│   │   ├── offers.py            # Offers management
│   │   ├── orders.py            # Order management
│   │   └── public.py            # Public endpoints for customers (no auth)
│   ├── services/                # Business logic
│   │   ├── auth_service.py
│   │   ├── restaurant_service.py
│   │   ├── menu_service.py
│   │   ├── whatsapp_service.py  # WhatsApp link generation + order message
│   │   └── subscription_service.py
│   └── utils/
│       ├── qr_generator.py      # QR code generation
│       └── slugify.py           # Restaurant slug generation
├── tests/
│   ├── test_auth.py
│   ├── test_menu.py
│   └── test_orders.py
├── .env.example
└── requirements.txt
```

## Tech Stack
- **FastAPI** — async, Python 3.11+
- **Motor** — async MongoDB driver
- **MongoDB** — main database (install locally on Windows)
- **Redis** — caching + rate limiting + sessions (install locally on Windows)
- **Pydantic v2** — validation and models
- **python-jose** — JWT tokens
- **passlib[bcrypt]** — password hashing
- **qrcode** — QR code generation
- **python-slugify** — slug generation

## Run Commands (Windows)
```bash
# Install dependencies
pip install -r requirements.txt

# Start development server
uvicorn app.main:app --reload

# Run tests
pytest tests/ -v

# Lint
ruff check app/
```

## Environment Variables (.env)
```
MONGODB_URL=mongodb://localhost:27017
MONGODB_DB_NAME=saas_menu
REDIS_URL=redis://localhost:6379
SECRET_KEY=1#2#3#4#5aA
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=30
```

## Coding Rules (Important)
- **Everything async** — use `async def` for all functions that interact with DB or Redis
- **Never use synchronous Motor calls** — always use async Motor
- **Dependency Injection** — use `Depends()` for auth and DB in every router
- **Never return sensitive data** — never return `password_hash` in responses
- **ObjectId** — always convert `_id` to string before returning data
- **Error handling** — use `HTTPException` with clear status codes
- **Comments in English**

## Multi-tenancy Model
- Each restaurant has a unique `restaurant_id` and unique `slug`
- The slug is used in the public menu URL: `/menu/{slug}`
- Restaurant owner role: `restaurant_owner`
- SaaS platform owner role: `saas_admin`

## Subscription Model
- The `saas_admin` creates restaurants and manually sets a subscription expiry date
- No plan tiers — access is simply active or not based on the expiry date
- Subscription fields: `start_date`, `expires_at`, `status` (active / expired / suspended)
- The system auto-checks expiry on each public menu request and returns 403 if expired

## WhatsApp Logic
Order link is generated as:
```
https://wa.me/{restaurant_whatsapp}?text={encoded_message}
```
Message contains: customer name, phone number, ordered items, notes, total price

## Git Rules
- Short and descriptive commit messages
- Main branch: `main`
- Feature branches: `feature/feature-name`
- Never commit the `.env` file
