from fastapi import APIRouter

from app.events import broker


router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {
        "status": "ok",
        "realtime": "redis" if broker.redis_connected else "database_polling",
    }
