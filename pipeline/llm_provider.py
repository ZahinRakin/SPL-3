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
import urllib.request
from typing import List

from core.config import settings
from core.logger import logger

# ── read config once at import time ──────────────────────────────────────────

LLM_PROVIDER   = settings.LLM_PROVIDER.lower().strip()
EMBED_PROVIDER = settings.EMBED_PROVIDER.lower().strip()

GEMINI_API_KEY     = settings.GEMINI_API_KEY
GEMINI_MODEL       = settings.GEMINI_MODEL
GEMINI_EMBED_MODEL = settings.GEMINI_EMBED_MODEL

GROQ_API_KEY = settings.GROQ_API_KEY
GROQ_MODEL   = settings.GROQ_MODEL

OLLAMA_BASE_URL    = settings.OLLAMA_BASE_URL
OLLAMA_EMBED_MODEL = settings.OLLAMA_EMBED_MODEL

logger.info(
    f"LLM provider: {LLM_PROVIDER!r} | embed provider: {EMBED_PROVIDER!r} | "
    f"llm model: {GROQ_MODEL if LLM_PROVIDER == 'groq' else GEMINI_MODEL!r} | "
    f"embed model: {OLLAMA_EMBED_MODEL if EMBED_PROVIDER == 'ollama' else GEMINI_EMBED_MODEL!r}"
)

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
        logger.info(f"Initialising Gemini model: {GEMINI_MODEL!r}")
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        _gemini_model = genai.GenerativeModel(GEMINI_MODEL)
    return _gemini_model


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        logger.info(f"Initialising Groq client: model={GROQ_MODEL!r}")
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
    logger.debug(f"LLM generate: provider={LLM_PROVIDER!r}, json_mode={json_mode}, prompt_len={len(prompt)}")
    async with _LLM_SEM:
        coro = (
            _groq_generate(prompt, json_mode, temperature)
            if LLM_PROVIDER == "groq"
            else _gemini_generate(prompt, json_mode, temperature)
        )
        try:
            result = await asyncio.wait_for(coro, timeout=_LLM_TIMEOUT)
            logger.debug(f"LLM generate complete: response_len={len(result)}")
            return result
        except asyncio.TimeoutError:
            logger.error(f"LLM generate timed out after {_LLM_TIMEOUT}s (provider={LLM_PROVIDER!r})")
            raise
        except Exception as exc:
            logger.error(f"LLM generate error (provider={LLM_PROVIDER!r}): {exc}", exc_info=True)
            raise


# ── Gemini generate ───────────────────────────────────────────────────────────

def _gemini_generate_sync(prompt: str, json_mode: bool, temperature: float) -> str:
    try:
        import google.generativeai as genai
        cfg = genai.types.GenerationConfig(temperature=temperature)
        if json_mode:
            cfg = genai.types.GenerationConfig(
                temperature=temperature,
                response_mime_type="application/json",
            )
        resp = _get_gemini_model().generate_content(prompt, generation_config=cfg)
        return resp.text
    except Exception as exc:
        logger.error(f"Gemini generate_content failed: {exc}", exc_info=True)
        raise


async def _gemini_generate(prompt: str, json_mode: bool, temperature: float) -> str:
    return await asyncio.to_thread(_gemini_generate_sync, prompt, json_mode, temperature)


# ── Groq generate ─────────────────────────────────────────────────────────────

def _groq_generate_sync(prompt: str, json_mode: bool, temperature: float) -> str:
    try:
        kwargs: dict = dict(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = _get_groq_client().chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""
    except Exception as exc:
        logger.error(f"Groq chat.completions.create failed: {exc}", exc_info=True)
        raise


async def _groq_generate(prompt: str, json_mode: bool, temperature: float) -> str:
    return await asyncio.to_thread(_groq_generate_sync, prompt, json_mode, temperature)


# ── Embeddings ────────────────────────────────────────────────────────────────

async def embed(text: str, task_type: str = "retrieval_document") -> List[float]:
    """Embed text using the configured embedding provider."""
    logger.debug(f"Embed: provider={EMBED_PROVIDER!r}, text_len={len(text)}")
    async with _EMBED_SEM:
        coro = (
            _ollama_embed(text)
            if EMBED_PROVIDER == "ollama"
            else _gemini_embed(text, task_type)
        )
        try:
            result = await asyncio.wait_for(coro, timeout=_EMBED_TIMEOUT)
            logger.debug(f"Embed complete: vector_dim={len(result)}")
            return result
        except asyncio.TimeoutError:
            logger.error(f"Embed timed out after {_EMBED_TIMEOUT}s (provider={EMBED_PROVIDER!r})")
            raise
        except Exception as exc:
            logger.error(f"Embed error (provider={EMBED_PROVIDER!r}): {exc}", exc_info=True)
            raise


# ── Gemini embed ──────────────────────────────────────────────────────────────

def _gemini_embed_sync(text: str, task_type: str) -> List[float]:
    try:
        import google.generativeai as genai
        result = genai.embed_content(
            model=GEMINI_EMBED_MODEL,
            content=text[:2048],
            task_type=task_type,
        )
        return result["embedding"]
    except Exception as exc:
        logger.error(f"Gemini embed_content failed: {exc}", exc_info=True)
        raise


async def _gemini_embed(text: str, task_type: str) -> List[float]:
    return await asyncio.to_thread(_gemini_embed_sync, text, task_type)


# ── Ollama embed (no extra dependency — pure stdlib HTTP) ─────────────────────

def _ollama_embed_sync(text: str) -> List[float]:
    try:
        payload = _json.dumps({"model": OLLAMA_EMBED_MODEL, "prompt": text[:2048]}).encode()
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return _json.loads(resp.read())["embedding"]
    except Exception as exc:
        logger.error(f"Ollama embed request to {OLLAMA_BASE_URL} failed: {exc}", exc_info=True)
        raise


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
