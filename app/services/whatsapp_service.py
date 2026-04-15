from decimal import Decimal
from typing import Any
from urllib.parse import quote

from app.models.order import OrderItem

_SEP = "━━━━━━━━━━━━━━━"

# العملات المدعومة
CURRENCY_SYMBOLS: dict[str, str] = {
    "IQD": "د.ع",
    "USD": "$",
}


def _clean_number(whatsapp_number: str) -> str:
    """Strip spaces, dashes, and leading + so wa.me gets a plain E.164 number."""
    return whatsapp_number.replace("+", "").replace(" ", "").replace("-", "")


def _build_message(
    customer_name: str,
    customer_phone: str,
    items: list[OrderItem],
    total: Decimal,
    notes: str | None,
    currency_symbol: str = "د.ع",
    discount_amount: Decimal = Decimal("0"),
    delivery_info: dict[str, Any] | None = None,
) -> str:
    lines = [
        "👤 معلومات العميل:",
        f"الاسم: {customer_name}",
        f"رقم الهاتف: {customer_phone}",
        "",
        _SEP,
        "🛒 تفاصيل الطلب:",
        "",
    ]

    for item in items:
        label = f"{item.name} ({item.variant_name})" if item.variant_name else item.name
        lines.append(f"• {label} ×{item.quantity} — {item.price:.2f} {currency_symbol}")

    lines.append("")
    lines.append(_SEP)
    lines.append("💰 ملخص الدفع:")
    lines.append("")

    if discount_amount > 0:
        original = total + discount_amount
        lines.append(f"المجموع قبل الخصم: {original:.2f} {currency_symbol}")
        lines.append(f"الخصم: -{discount_amount:.2f} {currency_symbol}")

    lines.append(f"الإجمالي النهائي: {total:.2f} {currency_symbol}")

    if notes:
        lines.append("")
        lines.append(_SEP)
        lines.append("📝 ملاحظات:")
        lines.append(notes)

    # Append delivery info if configured and available
    if delivery_info and delivery_info.get("available") and delivery_info.get("message"):
        lines.append("")
        lines.append(_SEP)
        lines.append("🚚 معلومات التوصيل:")
        lines.append(delivery_info["message"])

    return "\n".join(lines)


def generate_whatsapp_link(
    whatsapp_number: str,
    customer_name: str,
    customer_phone: str,
    items: list[OrderItem],
    total: Decimal,
    notes: str | None = None,
    currency_code: str = "IQD",
    discount_amount: Decimal = Decimal("0"),
    delivery_info: dict[str, Any] | None = None,
) -> str:
    """Build a formatted order message and return a wa.me deep-link.

    Args:
        whatsapp_number: Restaurant's WhatsApp number (any format).
        customer_name:   Customer's full name.
        customer_phone:  Customer's phone number.
        items:           Resolved order line items with name, price, quantity.
        total:           Pre-calculated order total.
        notes:           Optional customer notes.

    Returns:
        A fully encoded https://wa.me/{number}?text={message} URL.
    """
    symbol = CURRENCY_SYMBOLS.get(currency_code, currency_code)
    message = _build_message(customer_name, customer_phone, items, total, notes, symbol, discount_amount, delivery_info)
    number = _clean_number(whatsapp_number)
    return f"https://wa.me/{number}?text={quote(message)}"
