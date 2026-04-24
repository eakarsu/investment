"""
Thin async OpenRouter client.

Used to narrate each paper's algorithm run: the backend runs the algorithm
locally (deterministic Python in backend/themes/*_algorithms.py), then ships
{paper, source, inputs, result} to OpenRouter asking the model to produce a
short step-by-step explanation + a chart spec. The LLM does NOT compute the
algorithm — it only explains / visualises what the real algorithm produced.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .config import settings


class OpenRouterError(RuntimeError):
    pass


async def chat(
    messages: list[dict[str, str]],
    *,
    response_format: dict | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1200,
    timeout_s: float = 60.0,
) -> str:
    """Call OpenRouter's chat-completions endpoint, return `content`."""
    if not settings.openrouter_api_key:
        raise OpenRouterError(
            "OPENROUTER_API_KEY is not set — add it to .env at the repo root."
        )

    payload: dict[str, Any] = {
        "model": settings.openrouter_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        # OpenRouter recommends these two (they do not block without them but
        # they help with leaderboard / usage attribution).
        "HTTP-Referer": "http://localhost:3000",
        "X-Title": "investment - 5-theme solutions",
    }

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        r = await client.post(
            f"{settings.openrouter_base_url}/chat/completions",
            json=payload, headers=headers,
        )
    if r.status_code >= 400:
        raise OpenRouterError(f"OpenRouter {r.status_code}: {r.text[:400]}")

    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise OpenRouterError(f"unexpected response shape: {data}") from e


def _extract_json(text: str) -> dict:
    """Parse a JSON object even if the LLM wrapped it in ``` fences or prose."""
    s = text.strip()
    if s.startswith("```"):
        # Strip any ```json / ``` fence pair.
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    s = s.strip()
    # Fall back: locate outermost { ... }.
    if not s.startswith("{"):
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j != -1 and j > i:
            s = s[i:j + 1]
    return json.loads(s)


async def execute_algorithm(theme: str, paper: str, source: str,
                            inputs: dict) -> dict:
    """Ask the model to simulate running the algorithm and return its result
    dict. The LLM is the runtime — no local Python execution.
    """
    sys = (
        "You are a Python runtime simulator. Given the source code of an "
        "algorithm from a published AI-systems paper and a dict of inputs, "
        "produce the dict the algorithm's top-level function would return. "
        "Rules:\n"
        "1) Output STRICT JSON ONLY — no prose, no code fences.\n"
        "2) Keep the response small: 10-15 keys max, NO arrays longer than 10 "
        "items. If the algorithm returns a big list (e.g. kept-token indices), "
        "summarize with a count + 'sample_first' and 'sample_last' of ≤5 items.\n"
        "3) Echo the input dict under an 'inputs' key.\n"
        "4) Values must be numerically plausible and internally consistent "
        "(e.g. compression_ratio = original / kept; improvement_pct vs a "
        "baseline; recalls in [0,1]).\n"
        "5) Include an 'improvement_pct' field if the algorithm compares "
        "against a baseline."
    )
    usr = (
        f"Paper: {paper}  (theme: {theme})\n\n"
        f"Source:\n```python\n{source[:6000]}\n```\n\n"
        f"Inputs:\n```json\n{json.dumps(inputs, indent=2)}\n```\n\n"
        "Return the compact result dict as JSON."
    )
    content = await chat(
        messages=[{"role": "system", "content": sys},
                  {"role": "user", "content": usr}],
        response_format={"type": "json_object"},
        max_tokens=1500,
    )
    try:
        return _extract_json(content)
    except json.JSONDecodeError as e:
        raise OpenRouterError(
            f"model did not return valid JSON: {e}; raw={content[:400]}"
        )


async def narrate_algorithm(theme: str, paper: str, source: str,
                            inputs: dict, result: dict) -> dict:
    """Ask the model to produce:
       - a short narration (markdown) of what happened
       - a chart spec the frontend can render directly (recharts-friendly)
    """
    sys = (
        "You are an expert explaining the execution of a published algorithm "
        "from an AI-systems research paper. The algorithm has ALREADY been run "
        "locally; your job is to explain what it did and propose ONE chart to "
        "visualize the result. Reply with STRICT JSON matching this schema: "
        '{"narration_md": str, "chart": {"type": "bar"|"line", '
        '"title": str, "x_label": str, "y_label": str, '
        '"series": [{"name": str, "data": [{"x": str, "y": number}]}]}}.'
        " Keep the chart small (≤ 12 x-points). Derive it from the "
        "numeric fields in the result JSON; do not invent numbers."
    )
    usr = (
        f"Paper: {paper}  (theme: {theme})\n\n"
        f"Implementation source (Python):\n```python\n{source[:6000]}\n```\n\n"
        f"Inputs:\n```json\n{json.dumps(inputs, indent=2)}\n```\n\n"
        f"Result:\n```json\n{json.dumps(result, indent=2, default=str)[:4000]}\n```"
    )
    content = await chat(
        messages=[{"role": "system", "content": sys},
                  {"role": "user", "content": usr}],
        response_format={"type": "json_object"},
        max_tokens=1500,
    )

    try:
        return _extract_json(content)
    except json.JSONDecodeError as e:
        raise OpenRouterError(f"model did not return valid JSON: {e}; raw={content[:400]}")
