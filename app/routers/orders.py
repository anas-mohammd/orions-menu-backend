from fastapi import APIRouter

# Order management endpoints are reserved for a future admin/owner dashboard.
# Customer orders are placed via POST /public/menu/{slug}/order.
router = APIRouter()
