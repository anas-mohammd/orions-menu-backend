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
        err_type = err.get("type", "")
        ctx = err.get("ctx", {})
        msg = err.get("msg", "")

        if err_type == "missing":
            err["msg"] = "هذا الحقل مطلوب"
        elif err_type == "string_too_short":
            err["msg"] = f"يجب أن يتكون من {ctx.get('min_length', '')} أحرف على الأقل"
        elif err_type == "string_too_long":
            err["msg"] = f"يجب أن لا يتجاوز {ctx.get('max_length', '')} حرفاً"
        elif err_type == "greater_than":
            err["msg"] = f"يجب أن يكون أكبر من {ctx.get('gt', '')}"
        elif err_type == "greater_than_equal":
            err["msg"] = f"يجب أن يكون {ctx.get('ge', '')} أو أكثر"
        elif err_type == "less_than_equal":
            err["msg"] = f"يجب أن لا يتجاوز {ctx.get('le', '')}"
        elif err_type == "less_than":
            err["msg"] = f"يجب أن يكون أقل من {ctx.get('lt', '')}"
        elif err_type == "decimal_max_places":
            err["msg"] = f"يُسمح بـ {ctx.get('decimal_places', '')} خانات عشرية كحد أقصى"
        elif err_type in ("int_parsing", "int_type"):
            err["msg"] = "يجب أن يكون رقماً صحيحاً"
        elif err_type in ("float_parsing", "float_type", "decimal_parsing", "decimal_type"):
            err["msg"] = "يجب أن يكون رقماً"
        elif err_type == "string_type":
            err["msg"] = "يجب أن يكون نصاً"
        elif err_type == "bool_type":
            err["msg"] = "يجب أن تكون القيمة صح أو خطأ"
        elif err_type == "literal_error":
            expected = ctx.get("expected", "")
            err["msg"] = f"القيمة يجب أن تكون إحدى: {expected}"
        elif err_type == "enum":
            err["msg"] = "القيمة المدخلة غير مدعومة"
        elif err_type in ("datetime_type", "datetime_parsing", "datetime_from_date_parsing"):
            err["msg"] = "تاريخ غير صالح، يرجى إدخال تاريخ صحيح"
        elif err_type in ("url_type", "url_parsing", "url_scheme"):
            err["msg"] = "الرابط غير صالح"
        elif err_type in ("list_type",):
            err["msg"] = "يجب أن يكون قائمة"
        elif err_type == "list_min_length":
            err["msg"] = f"يجب أن تحتوي القائمة على {ctx.get('min_length', '')} عنصر على الأقل"
        elif err_type == "value_error":
            if "email" in msg.lower():
                err["msg"] = "البريد الإلكتروني غير صحيح"
            else:
                # رسائل model_validator — مكتوبة بالعربي مباشرة
                err["msg"] = msg.removeprefix("Value error, ")
        else:
            err["msg"] = "القيمة المدخلة غير صحيحة"

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
