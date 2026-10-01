"""Gemini text generation over the REST API.

httpx rather than the google SDK: this is one POST, and the SDK would be a
second HTTP stack in an image that already ships httpx.
"""

import logging

import httpx

from app.config import get_settings


logger = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

# How many times each tool may be called in one reply. Per tool rather than one
# shared budget: a customer asking about a product *and* two shop policies needs
# several different tools, and a single pooled limit would let the product search
# spend the whole allowance before the rules were ever looked up.
MAX_CALLS_PER_TOOL = 5

# How many request/response rounds the loop may take before the model has to
# answer. Derived, not tuned: with a per-tool cap the real ceiling is the number
# of tools, and this only has to be generous enough not to bind first. The +1
# leaves a round for the answer itself.
MAX_TOOL_ROUNDS = 12


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


def run_tool(part: dict, tools: dict, used: dict | None = None) -> dict:
    """One tool call's result, as the model should see it.

    Takes the whole part and reads the call out of it, so callers can keep
    passing the parts around untouched.

    ``used`` counts calls per tool name across this reply. Once a tool has been
    called MAX_CALLS_PER_TOOL times it is refused, and the refusal is returned to
    the model as a result rather than raised - being told "you have used this one
    up, answer with what you have" is something it can act on, where silence
    would leave it retrying until the round limit ran out.

    Other failures are reported the same way: the model can apologise or try a
    different tool, where an exception would cost the customer the whole reply.
    """
    call = part.get("functionCall") or part
    name = call.get("name") or ""
    handler = tools.get(name)
    if handler is None:
        return {"error": f"unknown tool: {name}"}

    if used is not None:
        if used.get(name, 0) >= MAX_CALLS_PER_TOOL:
            logger.warning("tool %s hit its %s-call limit", name, MAX_CALLS_PER_TOOL)
            return {
                "error": (
                    f"'{name}' bu javobda {MAX_CALLS_PER_TOOL} marta ishlatildi, "
                    "boshqa chaqirilmaydi. Shu paytgacha topilgan ma'lumot bilan javob ber."
                )
            }
        used[name] = used.get(name, 0) + 1

    try:
        result = handler(**(call.get("args") or {}))
    except Exception as exc:
        logger.exception("tool %s failed", name)
        return {"error": str(exc)}

    found = result.get("found") if isinstance(result, dict) else None
    count = result.get("count") if isinstance(result, dict) else None
    logger.info(
        "tool %s called args=%r found=%s count=%s",
        name, call.get("args") or {}, found, count,
    )
    return result


def generate(
    prompt: str | list[dict],
    system_instruction: str | None = None,
    timeout: float = 45.0,
    tools: dict | None = None,
    declarations: list[dict] | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> str:
    """The model's answer, after running any tools it asks for along the way.

    ``prompt`` is either the customer's message or a full contents list when the
    caller has history to replay.
    """
    settings = get_settings()
    effective_key = (api_key or "").strip() or settings.gemini_api_key
    if not effective_key:
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
    model_name = model or settings.gemini_model
    if model_name.startswith("gemini-3"):
        # Thinking is billed as output, several times the input rate. Measured on
        # the shop's own questions, "low" gave the same answers - same products,
        # same recommendation - for about half the thinking tokens. Gemini 3
        # only: older models reject the field, and a rejected request is a
        # customer with no reply at all.
        body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "low"}
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    if declarations:
        body["tools"] = [{"functionDeclarations": declarations}]

    payload: dict = {}

    # Calls made per tool name, for the per-tool limit in run_tool. One dict for
    # the whole reply: the budget is per tool, not per round, so a tool called
    # once in each of five rounds is just as spent as one called five times over.
    used_calls: dict[str, int] = {}

    # Bounded on purpose: a model that keeps asking for tools would otherwise
    # hold the customer's chat open until the caller's own deadline killed it.
    for _ in range(MAX_TOOL_ROUNDS):
        response = httpx.post(
            f"{API_ROOT}/{model_name}:generateContent",
            # The key travels as a header, never as ?key= - a query string ends up in
            # proxy and access logs.
            headers={"x-goog-api-key": effective_key},
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
                            "response": run_tool(part, tools or {}, used_calls),
                        }
                    }
                    for part in calls
                ],
            }
        )

    # Out of rounds, and the last turn was itself another tool call - its
    # result is already appended to contents, but the model has never been
    # asked to turn that into words. Without this, extract_text(payload) reads
    # the tool-call turn, finds no text, and the customer gets silence even
    # though the second-to-last round's tool result had their answer.
    logger.warning("tool loop hit %s rounds without a final answer", MAX_TOOL_ROUNDS)
    body.pop("tools", None)  # forced: no more tool calls, only an answer
    response = httpx.post(
        f"{API_ROOT}/{model_name}:generateContent",
        headers={"x-goog-api-key": effective_key},
        json=body,
        timeout=timeout,
    )
    response.raise_for_status()
    return extract_text(response.json())
