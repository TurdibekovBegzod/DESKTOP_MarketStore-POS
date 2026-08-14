from app.celery_app import celery_app
from app.email_service import send_password_reset_code, send_signup_verification_code


@celery_app.task(
    name="send_password_reset_code",
    autoretry_for=(OSError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def send_password_reset_code_task(to_email: str, code: str) -> None:
    send_password_reset_code(to_email, code)


@celery_app.task(
    name="send_signup_verification_code",
    autoretry_for=(OSError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def send_signup_verification_code_task(to_email: str, code: str) -> None:
    send_signup_verification_code(to_email, code)
