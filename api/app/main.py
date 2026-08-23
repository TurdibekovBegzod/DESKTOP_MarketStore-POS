from fastapi import FastAPI

from app.config import get_settings
from app.routers import auth, health, sync, updates


settings = get_settings()
app = FastAPI(title=settings.app_name)

app.include_router(health.router)
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(sync.router, prefix=settings.api_prefix)
app.include_router(updates.router, prefix=settings.api_prefix)
