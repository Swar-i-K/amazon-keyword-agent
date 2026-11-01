"""
providers.py
------------
Which AI answers the idea prompts. Keys come from environment variables (GitHub secrets); a provider with no key is skipped.

  gemini      GEMINI_API_KEY      Google AI Studio free tier, OpenAI-compatible endpoint. Several model names are tried in
                                  order because model names get retired (a 404 moves on to the next name).
  groq        GROQ_API_KEY        same key as the other agents (llm.py)
  perplexity  PERPLEXITY_API_KEY  web-grounded RESEARCH step only (what shoppers ask). The API is pay-as-you-go, no free tier.

generate() walks the configured chain and returns (text, provider). research() asks Perplexity's Agent API and returns plain notes.
"""
from __future__ import annotations

import os
import re
import time

import requests

import llm

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
PERPLEXITY_ENDPOINT = "https://api.perplexity.ai/v1/agent"
DEFAULT_GEMINI_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite"]   # check AI Studio for current names


class ProviderError(RuntimeError):
    pass


def has_key(provider: str) -> bool:
    env = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "perplexity": "PERPLEXITY_API_KEY"}[provider]
    return bool((os.environ.get(env) or "").strip())


def _key(name: str) -> str:
    return (os.environ.get(name) or "").strip()


# ------------------------------------------------------------------ gemini
def gemini_chat(prompt: str, models: list, max_tokens: int, temperature: float = 0.7) -> str:
    key = _key("GEMINI_API_KEY")
    if not key:
        raise llm.LLMUnavailable("GEMINI_API_KEY is not set")
    problems = []
    for model in models or DEFAULT_GEMINI_MODELS:
        for attempt in range(2):
            resp = requests.post(
                GEMINI_ENDPOINT, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "temperature": temperature, "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": prompt}]}, timeout=180)
            if resp.status_code == 404:                      # model name retired or not on this key: try the next name
                problems.append(f"{model}: not found (404)")
                break
            if resp.status_code == 429:                      # free-tier limit: one short wait, then the next model
                problems.append(f"{model}: rate limited (429)")
                if attempt == 0:
                    time.sleep(min(float(resp.headers.get("retry-after", 10)) + 1, 30))
                    continue
                break
            if resp.status_code in (400, 401, 403):          # key or request problem: other model names will not help
                raise ProviderError(f"Gemini rejected the request ({resp.status_code}): {resp.text[:200]}")
            resp.raise_for_status()
            text = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
            if text.strip():
                return text
            problems.append(f"{model}: empty answer")
            break
    raise ProviderError("Gemini failed: " + "; ".join(problems))


# ------------------------------------------------------------------ perplexity (research)
def _agent_text(data: dict) -> str:
    """Answer text from an Agent API response: output_text if present, else the message parts in `output`."""
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"]
    parts = []
    for item in data.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            content = item.get("content")
            if isinstance(content, str):
                parts.append(content)
            for c in content if isinstance(content, list) else []:
                if isinstance(c, dict) and isinstance(c.get("text"), str):
                    parts.append(c["text"])
    return "\n".join(parts)


def research(prompt: str, effort: str = "fast", max_tokens: int = 1500) -> str:
    key = _key("PERPLEXITY_API_KEY")
    if not key:
        raise llm.LLMUnavailable("PERPLEXITY_API_KEY is not set")
    resp = requests.post(
        PERPLEXITY_ENDPOINT, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"effort": effort, "input": prompt, "tools": [{"type": "web_search"}], "max_output_tokens": max_tokens},
        timeout=180)
    if resp.status_code in (401, 402, 403):
        raise ProviderError(f"Perplexity rejected the request ({resp.status_code}); the API needs prepaid credits: {resp.text[:160]}")
    resp.raise_for_status()
    text = _agent_text(resp.json())
    text = re.sub(r"\[(?:[a-z]+:)?\d+\]", "", text)             # drop [1] / [web:1] citation markers
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        raise ProviderError("Perplexity returned no text")
    return text[:2500]


# ------------------------------------------------------------------ chain
def generate(prompt: str, cfg: dict, max_tokens: int, temperature: float = 0.7, exclude: set | None = None) -> tuple:
    """-> (text, provider_name). Tries cfg['ideas']['providers'] in order; providers without a key are skipped."""
    chain = [p for p in ((cfg.get("ideas") or {}).get("providers") or ["gemini", "groq"]) if p in ("gemini", "groq")]
    keyed = [p for p in chain if has_key(p)]
    if not keyed:
        raise llm.LLMUnavailable("No AI key is set (add GEMINI_API_KEY and/or GROQ_API_KEY)")
    exclude = exclude or set()
    if any(p not in exclude for p in keyed):                     # skip a provider whose last answer was unusable, if another exists
        keyed = [p for p in keyed if p not in exclude]
    errors = []
    for p in keyed:
        try:
            if p == "gemini":
                models = (cfg.get("gemini") or {}).get("models") or DEFAULT_GEMINI_MODELS
                return gemini_chat(prompt, models, max(max_tokens, 6000), temperature), "gemini"
            return llm.chat(prompt, cfg["llm"]["model"], max_tokens, temperature), "groq"
        except Exception as e:  # noqa: BLE001
            errors.append(f"{p}: {e}")
    raise ProviderError(" | ".join(errors))
