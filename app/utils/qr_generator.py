import base64
import io

import qrcode
from qrcode.image.pil import PilImage


def generate_qr_code(restaurant_slug: str, base_url: str) -> str:
    """Generate a QR code pointing to {base_url}/menu/{slug}.

    Args:
        restaurant_slug: The restaurant's unique slug.
        base_url:        The frontend base URL (e.g. https://orionmenu.com).

    Returns:
        A base64-encoded PNG string (no data-URI prefix).
    """
    url = f"{base_url.rstrip('/')}/menu/{restaurant_slug}"

    qr = qrcode.QRCode(
        version=None,          # auto-size to fit the data
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img: PilImage = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    return base64.b64encode(buffer.read()).decode("utf-8")
