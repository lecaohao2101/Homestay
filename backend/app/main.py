import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import settings
from app.core.middlewares import setup_middlewares
from app.db.session import close_mongodb, init_mongodb
from app.services.refund_reconcile_scheduler import refund_reconcile_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_mongodb()
    app.state.refund_reconcile_task = None
    if settings.REFUND_RECONCILE_JOB_ENABLED:
        app.state.refund_reconcile_task = asyncio.create_task(refund_reconcile_worker())
    try:
        yield
    finally:
        task = getattr(app.state, "refund_reconcile_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        close_mongodb()


def create_application() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="0.1.0",
        lifespan=lifespan,
    )
    setup_middlewares(app)
    app.include_router(api_router, prefix=settings.API_V1_STR)
    return app


app = create_application()
