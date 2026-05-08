"""
Unified LLM + embedding provider.

Configure via .env:
    LLM_PROVIDER=gemini        # or: groq
    EMBED_PROVIDER=gemini      # or: ollama

    # Gemini
    GEMINI_API_KEY=...
    GEMINI_MODEL=gemini-2.0-flash
    GEMINI_EMBED_MODEL=models/text-embedding-004

    # Groq
    GROQ_API_KEY=...
    GROQ_MODEL=llama-3.3-70b-versatile

    # Ollama (for embeddings when EMBED_PROVIDER=ollama)
    OLLAMA_BASE_URL=http://localhost:11434
    OLLAMA_EMBED_MODEL=nomic-embed-text
"""
import asyncio
import json as _json
import os
import urllib.request
from typing import List

# ── read config once at import time ──────────────────────────────────────────

LLM_PROVIDER   = os.getenv("LLM_PROVIDER",   "gemini").lower().strip()
EMBED_PROVIDER = os.getenv("EMBED_PROVIDER",  "gemini").lower().strip()

GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL      = os.getenv("GEMINI_MODEL",   "gemini-2.0-flash")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "models/text-embedding-004")

GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL    = os.getenv("GROQ_MODEL",   "llama-3.3-70b-versatile")

OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL",   "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# ── concurrency + timeout ─────────────────────────────────────────────────────

_LLM_SEM    = asyncio.Semaphore(3)   # max concurrent LLM calls
_EMBED_SEM  = asyncio.Semaphore(5)   # max concurrent embedding calls
_LLM_TIMEOUT   = 45.0
_EMBED_TIMEOUT = 30.0

# ── lazy singletons ───────────────────────────────────────────────────────────

_gemini_model = None
_groq_client  = None


def _get_gemini_model():
    global _gemini_model
    if _gemini_model is None:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        _gemini_model = genai.GenerativeModel(GEMINI_MODEL)
    return _gemini_model


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        from groq import Groq  # pip install groq
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


# ── LLM: generate ─────────────────────────────────────────────────────────────

async def generate(
    prompt: str,
    json_mode: bool = False,
    temperature: float = 0.1,
) -> str:
    """Call the configured LLM provider. Returns the raw text response."""
    async with _LLM_SEM:
        coro = (
            _groq_generate(prompt, json_mode, temperature)
            if LLM_PROVIDER == "groq"
            else _gemini_generate(prompt, json_mode, temperature)
        )
        return await asyncio.wait_for(coro, timeout=_LLM_TIMEOUT)


# ── Gemini generate ───────────────────────────────────────────────────────────

def _gemini_generate_sync(prompt: str, json_mode: bool, temperature: float) -> str:
    import google.generativeai as genai
    cfg = genai.types.GenerationConfig(temperature=temperature)
    if json_mode:
        cfg = genai.types.GenerationConfig(
            temperature=temperature,
            response_mime_type="application/json",
        )
    resp = _get_gemini_model().generate_content(prompt, generation_config=cfg)
    return resp.text


async def _gemini_generate(prompt: str, json_mode: bool, temperature: float) -> str:
    return await asyncio.to_thread(_gemini_generate_sync, prompt, json_mode, temperature)


# ── Groq generate ─────────────────────────────────────────────────────────────

def _groq_generate_sync(prompt: str, json_mode: bool, temperature: float) -> str:
    kwargs: dict = dict(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = _get_groq_client().chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


async def _groq_generate(prompt: str, json_mode: bool, temperature: float) -> str:
    return await asyncio.to_thread(_groq_generate_sync, prompt, json_mode, temperature)


# ── Embeddings ────────────────────────────────────────────────────────────────

async def embed(text: str, task_type: str = "retrieval_document") -> List[float]:
    """Embed text using the configured embedding provider."""
    async with _EMBED_SEM:
        coro = (
            _ollama_embed(text)
            if EMBED_PROVIDER == "ollama"
            else _gemini_embed(text, task_type)
        )
        return await asyncio.wait_for(coro, timeout=_EMBED_TIMEOUT)


# ── Gemini embed ──────────────────────────────────────────────────────────────

def _gemini_embed_sync(text: str, task_type: str) -> List[float]:
    import google.generativeai as genai
    result = genai.embed_content(
        model=GEMINI_EMBED_MODEL,
        content=text[:2048],
        task_type=task_type,
    )
    return result["embedding"]


async def _gemini_embed(text: str, task_type: str) -> List[float]:
    return await asyncio.to_thread(_gemini_embed_sync, text, task_type)


# ── Ollama embed (no extra dependency — pure stdlib HTTP) ─────────────────────

def _ollama_embed_sync(text: str) -> List[float]:
    payload = _json.dumps({"model": OLLAMA_EMBED_MODEL, "prompt": text[:2048]}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return _json.loads(resp.read())["embedding"]


async def _ollama_embed(text: str) -> List[float]:
    return await asyncio.to_thread(_ollama_embed_sync, text)


# ── helpers for app.py health check ──────────────────────────────────────────

def active_api_key_set() -> bool:
    if LLM_PROVIDER == "groq":
        return bool(GROQ_API_KEY)
    return bool(GEMINI_API_KEY)


def provider_info() -> dict:
    return {
        "llm_provider":   LLM_PROVIDER,
        "llm_model":      GROQ_MODEL if LLM_PROVIDER == "groq" else GEMINI_MODEL,
        "embed_provider": EMBED_PROVIDER,
        "embed_model":    OLLAMA_EMBED_MODEL if EMBED_PROVIDER == "ollama" else GEMINI_EMBED_MODEL,
    }
