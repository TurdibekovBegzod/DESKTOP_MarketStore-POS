"""Gemini text generation over the REST API.

httpx rather than the google SDK: this is one POST, and the SDK would be a
second HTTP stack in an image that already ships httpx.
"""

import logging

import httpx

from app.config import get_settings


logger = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

# How many times the model may ask for tools before it has to answer.
MAX_TOOL_ROUNDS = 5


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


def extract_function_calls(payload: dict) -> list[dict]:
    """The model's tool-call parts for this turn, whole and unmodified.

    Whole parts, not just their functionCall: a 3.x part also carries a
    thoughtSignature, and the next request is rejected outright unless every
    part comes back exactly as it was received. One turn can hold several -
    Gemini calls independent tools in parallel. An empty list is the signal
    that the model stopped asking and wrote its answer.
    """
    for candidate in payload.get("candidates") or []:
        parts = (candidate.get("content") or {}).get("parts") or []
        calls = [part for part in parts if part.get("functionCall")]
        if calls:
            return calls
    return []


def run_tool(part: dict, tools: dict) -> dict:
    """One tool call's result, as the model should see it.

    Takes the whole part and reads the call out of it, so callers can keep
    passing the parts around untouched.

    Failures are reported rather than raised: the model can apologise or try a
    different tool, where an exception would cost the customer the whole reply.
    """
    call = part.get("functionCall") or part
    name = call.get("name") or ""
    handler = tools.get(name)
    if handler is None:
        return {"error": f"unknown tool: {name}"}

    try:
        return handler(**(call.get("args") or {}))
    except Exception as exc:
        logger.exception("tool %s failed", name)
        return {"error": str(exc)}


def generate(
    prompt: str | list[dict],
    system_instruction: str | None = None,
    timeout: float = 45.0,
    tools: dict | None = None,
    declarations: list[dict] | None = None,
    model: str | None = None,
) -> str:
    """The model's answer, after running any tools it asks for along the way.

    ``prompt`` is either the customer's message or a full contents list when the
    caller has history to replay.
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        raise GeminiNotConfiguredError("GEMINI_API_KEY is not set")

    if isinstance(prompt, str):
        contents = [{"role": "user", "parts": [{"text": prompt}]}]
    else:
        contents = list(prompt)

    body: dict = {
        "contents": contents,
        # A thinking model spends part of this budget before it writes a word, and
        # a run that hits the ceiling returns no text at all. The budget is
        # therefore generous; the reply's own length is capped in agent.py.
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
    }
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    if declarations:
        body["tools"] = [{"functionDeclarations": declarations}]

    payload: dict = {}

    # Bounded on purpose: a model that keeps asking for tools would otherwise
    # hold the customer's chat open until the caller's own deadline killed it.
    for _ in range(MAX_TOOL_ROUNDS):
        response = httpx.post(
            f"{API_ROOT}/{model or settings.gemini_model}:generateContent",
            # The key travels as a header, never as ?key= - a query string ends up in
            # proxy and access logs.
            headers={"x-goog-api-key": settings.gemini_api_key},
            json=body,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()

        calls = extract_function_calls(payload)
        if not calls:
            return extract_text(payload)

        # The model's parts go back verbatim - see extract_function_calls.
        contents.append({"role": "model", "parts": calls})
        contents.append(
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": (part.get("functionCall") or {}).get("name"),
                            "response": run_tool(part, tools or {}),
                        }
                    }
                    for part in calls
                ],
            }
        )

    # Out of rounds. Whatever text the last turn produced beats saying nothing.
    logger.warning("tool loop hit %s rounds without a final answer", MAX_TOOL_ROUNDS)
    return extract_text(payload)
