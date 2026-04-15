from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.db.mongodb import connect_db, disconnect_db
from app.db.redis import connect_redis, disconnect_redis
from app.routers import auth, admin, restaurants, menu, offers, orders, public, uploads
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi import Request

_TAGS_METADATA = [
    {
        "name": "Auth",
        "description": "Registration, login, token refresh, and current-user profile.",
    },
    {
        "name": "Admin",
        "description": (
            "SaaS platform management. **Requires `saas_admin` role.** "
            "Create restaurants, manage subscriptions, view platform statistics."
        ),
    },
    {
        "name": "Restaurants",
        "description": (
            "Restaurant owner profile management. **Requires `restaurant_owner` role.** "
            "View and update restaurant info, generate the QR code."
        ),
    },
    {
        "name": "Menu",
        "description": (
            "Category and menu item management. **Requires `restaurant_owner` role.** "
            "Create, update, delete, and reorder categories and items."
        ),
    },
    {
        "name": "Offers",
        "description": (
            "Discount offer management. **Requires `restaurant_owner` role.** "
            "Create percentage or fixed-amount offers tied to specific items or the whole menu."
        ),
    },
    {
        "name": "Orders",
        "description": "Reserved for future order management endpoints.",
    },
    {
        "name": "Public",
        "description": (
            "Unauthenticated endpoints consumed by customers. "
            "Fetch the public menu (cached 5 min) and submit orders via WhatsApp link."
        ),
    },
    {
        "name": "Health",
        "description": "Service liveness check.",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    await connect_redis()
    yield
    await disconnect_db()
    await disconnect_redis()


app = FastAPI(
    title="OrionMenu API",
    description=(
        "SaaS platform that lets restaurant owners publish a digital menu. "
        "Customers browse the menu and send their order directly via WhatsApp.\n\n"
        "## Authentication\n"
        "Protected endpoints require a Bearer token obtained from `POST /auth/login`.\n\n"
        "## Roles\n"
        "| Role | Access |\n"
        "|---|---|\n"
        "| `saas_admin` | Full platform management via `/admin/*` |\n"
        "| `restaurant_owner` | Own restaurant, menu, and offers |\n"
        "| *(none)* | Public menu and order endpoints |"
    ),
    version="1.0.0",
    openapi_tags=_TAGS_METADATA,
    lifespan=lifespan,
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    translated_errors = []
    
    for err in errors:
        msg = err.get("msg", "")
        # ترجمة الأخطاء الشائعة للعربية
        if "String should have at least" in msg:
            min_len = err.get("ctx", {}).get("min_length", "")
            err["msg"] = f"يجب أن يتكون من {min_len} أحرف/أرقام على الأقل"
        elif msg == "Field required":
            err["msg"] = "هذا الحقل مطلوب"
        elif "value is not a valid" in msg.lower():
            err["msg"] = "القيمة المدخلة غير صحيحة"
        elif "value is not a valid email" in msg.lower():
            err["msg"] = "البريد الإلكتروني غير صحيح"
            
        translated_errors.append(err)
        
    return JSONResponse(
        status_code=422,
        content={"detail": translated_errors},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost(:\d+)?|([a-zA-Z0-9-]+\.)*orionsmenu\.com)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,        prefix="/auth",        tags=["Auth"])
app.include_router(admin.router,       prefix="/admin",       tags=["Admin"])
app.include_router(restaurants.router, prefix="/restaurants", tags=["Restaurants"])
app.include_router(menu.router,        prefix="/menu",        tags=["Menu"])
app.include_router(offers.router,      prefix="/offers",      tags=["Offers"])
app.include_router(orders.router,      prefix="/orders",      tags=["Orders"])
app.include_router(public.router,      prefix="/public",      tags=["Public"])
app.include_router(uploads.router,     prefix="/upload",      tags=["Uploads"])

# Serve uploaded images — must be mounted AFTER all routers
_uploads_dir = Path("uploads")
_uploads_dir.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_uploads_dir)), name="uploads")


@app.get(
    "/health",
    tags=["Health"],
    summary="Health check",
    response_model=dict,
)
async def health_check():
    """Returns `{"status": "ok"}` when the service is running."""
    return {"status": "ok"}
