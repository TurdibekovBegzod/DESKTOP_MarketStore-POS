"""Gemini text generation over the REST API.

httpx rather than the google SDK: this is one POST, and the SDK would be a
second HTTP stack in an image that already ships httpx.
"""

import httpx

from app.config import get_settings


API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiNotConfiguredError(RuntimeError):
    """No API key. Retrying cannot fix it, so the task must not retry."""


def extract_text(payload: dict) -> str:
    """The answer, or "" when the model returned nothing usable.

    An empty string is a real outcome, not an error: a prompt caught by a safety
    filter comes back as a candidate with no parts, and the caller's job then is
    to stay silent rather than to invent something.
    """
    for candidate in payload.get("candidates") or []:
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text") or "" for part in parts).strip()
        if text:
            return text
    return ""


def generate(prompt: str, system_instruction: str | None = None, timeout: float = 45.0) -> str:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise GeminiNotConfiguredError("GEMINI_API_KEY is not set")

    body: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        # A thinking model spends part of this budget before it writes a word, and
        # a run that hits the ceiling returns no text at all. The budget is
        # therefore generous; the reply's own length is capped in agent.py.
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
    }
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    response = httpx.post(
        f"{API_ROOT}/{settings.gemini_model}:generateContent",
        # The key travels as a header, never as ?key= - a query string ends up in
        # proxy and access logs.
        headers={"x-goog-api-key": settings.gemini_api_key},
        json=body,
        timeout=timeout,
    )
    response.raise_for_status()
    return extract_text(response.json())
