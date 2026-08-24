from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.config import get_settings
from app.routers import auth, health, sync, updates


settings = get_settings()
app = FastAPI(title=settings.app_name)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.resolved_trusted_hosts())

app.include_router(health.router)
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(sync.router, prefix=settings.api_prefix)
app.include_router(updates.router, prefix=settings.api_prefix)


@app.get("/install.sh")
async def root_install_sh():
    return await updates.get_install_sh()


@app.get("/install.ps1")
async def root_install_ps1():
    return await updates.get_install_ps1()
