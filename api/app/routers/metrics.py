"""Prometheus metrics for the Grafana Cloud agent.

Mounted only when ``METRICS_TOKEN`` is set, and then only served to a caller
presenting that token: the ngrok tunnel forwards every path, so an open
/metrics would hand our request volumes and endpoint names to anyone.
"""

from fastapi import APIRouter, Header, HTTPException, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
import secrets

from app.config import get_settings


router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
def metrics(authorization: str | None = Header(default=None)):
    expected = get_settings().metrics_token
    if not expected:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization.split(" ", 1)[1].strip()
    if not presented or not secrets.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Metrics token is required",
        )

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
