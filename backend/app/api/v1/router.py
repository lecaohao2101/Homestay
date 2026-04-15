from fastapi import APIRouter

from app.api.v1.admin_dashboard import router as admin_dashboard_router
from app.api.v1.auth import router as auth_router
from app.api.v1.bookings import router as bookings_router
from app.api.v1.coupons import router as coupons_router
from app.api.v1.health import router as health_router
from app.api.v1.media import router as media_router
from app.api.v1.payments import router as payments_router
from app.api.v1.properties import router as properties_router
from app.api.v1.refunds import router as refunds_router
from app.api.v1.search import router as search_router
from app.api.v1.users import router as users_router
from app.api.v1.reviews import router as reviews_router
from app.api.v1.wishlist import router as wishlist_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(admin_dashboard_router)
router.include_router(health_router)
router.include_router(properties_router)
router.include_router(bookings_router)
router.include_router(coupons_router)
router.include_router(payments_router)
router.include_router(refunds_router)
router.include_router(search_router)
router.include_router(media_router)
router.include_router(reviews_router)
router.include_router(wishlist_router)
