from celery import Celery

from app.config import get_settings


settings = get_settings()

celery_app = Celery(
    "marketstore_api",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_queue="marketstore",
    broker_connection_retry_on_startup=True,
    worker_prefetch_multiplier=1,
)
