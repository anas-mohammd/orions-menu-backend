import io
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from PIL import Image

from app.core.dependencies import get_db, require_restaurant_owner
from app.models.user import UserResponse
from app.routers.restaurants import _get_owner_restaurant_or_404

router = APIRouter()

UPLOAD_DIR = Path("uploads")
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# Max dimensions per image type (width, height)
SIZE_MAP = {
    "logo":     (400, 400),
    "category": (800, 600),
    "item":     (800, 600),
}


def _to_webp(content: bytes, max_w: int, max_h: int, quality: int = 85) -> bytes:
    """Open raw bytes, resize (keep aspect ratio), convert to WebP."""
    img = Image.open(io.BytesIO(content))

    # Normalise colour mode for WebP
    if img.mode == "P":
        img = img.convert("RGBA")
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    img.thumbnail((max_w, max_h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=quality, method=6)
    return buf.getvalue()


@router.post(
    "/image",
    summary="Upload an image",
    description=(
        "Accepts a JPEG / PNG / WebP / GIF file (max 5 MB), "
        "resizes it, converts it to WebP, and stores it under "
        "`uploads/{restaurant_id}/{image_type}/`. "
        "Returns the relative URL that can be stored in `image_url` / `logo_url` fields.\n\n"
        "`image_type` must be one of: **logo**, **category**, **item**."
    ),
    responses={
        200: {"description": "Upload successful"},
        400: {"description": "Invalid file or image_type"},
        401: {"description": "Missing or invalid token"},
        403: {"description": "restaurant_owner role required"},
        404: {"description": "Restaurant not found"},
    },
)
async def upload_image(
    file: UploadFile = File(..., description="Image file to upload"),
    image_type: str = Query(..., description="logo | category | item"),
    restaurant_id: str | None = Query(default=None, description="Target restaurant ID when owner has multiple"),
    current_user: UserResponse = Depends(require_restaurant_owner),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    # Validate image_type
    if image_type not in SIZE_MAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image_type. Allowed values: logo, category, item",
        )

    # Validate content type
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type. Please upload a JPEG, PNG, WebP, or GIF image.",
        )

    # Read & size-check
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File exceeds the 5 MB size limit.",
        )

    # Verify restaurant ownership
    doc = await _get_owner_restaurant_or_404(db, current_user.id, restaurant_id)
    rid = str(doc["_id"])

    # Process image
    try:
        max_w, max_h = SIZE_MAP[image_type]
        webp_bytes = _to_webp(content, max_w, max_h)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not process the image. Make sure it is a valid image file.",
        )

    # Save to disk: uploads/{restaurant_id}/{image_type}/{uuid}.webp
    dest_dir = UPLOAD_DIR / rid / image_type
    dest_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}.webp"
    (dest_dir / filename).write_bytes(webp_bytes)

    return {"url": f"/uploads/{rid}/{image_type}/{filename}"}
