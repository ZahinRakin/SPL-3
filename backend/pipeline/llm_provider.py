"""
Unified LLM + embedding provider.

LLM:        Groq   (configure GROQ_API_KEY and GROQ_MODEL in .env)
Embeddings: Ollama (configure OLLAMA_BASE_URL and OLLAMA_EMBED_MODEL in .env)
            Run:  ollama pull nomic-embed-text && ollama serve
"""
import asyncio
import json as _json
import urllib.request
from typing import List

from backend.core.config import settings
from backend.core.logger import logger

# ── config ────────────────────────────────────────────────────────────────────

GROQ_API_KEY = settings.GROQ_API_KEY
GROQ_MODEL   = settings.GROQ_MODEL

OLLAMA_BASE_URL    = settings.OLLAMA_BASE_URL
OLLAMA_EMBED_MODEL = settings.OLLAMA_EMBED_MODEL

logger.info(f"LLM: Groq model={GROQ_MODEL!r} | Embeddings: Ollama model={OLLAMA_EMBED_MODEL!r} at {OLLAMA_BASE_URL}")

# ── concurrency + timeout ─────────────────────────────────────────────────────

_LLM_SEM       = asyncio.Semaphore(3)
_EMBED_SEM     = asyncio.Semaphore(5)
_LLM_TIMEOUT   = 45.0
_EMBED_TIMEOUT = 30.0

# ── lazy singleton ────────────────────────────────────────────────────────────

_groq_client = None


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        logger.info(f"Initialising Groq client: model={GROQ_MODEL!r}")
        from groq import Groq
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


# ── LLM: generate ─────────────────────────────────────────────────────────────

async def generate(
    prompt: str,
    json_mode: bool = False,
    temperature: float = 0.1,
) -> str:
    """Call Groq. Returns the raw text response."""
    logger.debug(f"generate: json_mode={json_mode}, prompt_len={len(prompt)}")
    async with _LLM_SEM:
        try:
            result = await asyncio.wait_for(
                _groq_generate(prompt, json_mode, temperature),
                timeout=_LLM_TIMEOUT,
            )
            logger.debug(f"generate complete: response_len={len(result)}")
            return result
        except asyncio.TimeoutError:
            logger.error(f"generate timed out after {_LLM_TIMEOUT}s")
            raise
        except Exception as exc:
            logger.error(f"generate failed: {exc}", exc_info=True)
            raise


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


# ── Embeddings (Ollama) ───────────────────────────────────────────────────────

async def embed(text: str, task_type: str = "retrieval_document") -> List[float]:
    """Embed text using Ollama."""
    logger.debug(f"embed: text_len={len(text)}")
    async with _EMBED_SEM:
        try:
            result = await asyncio.wait_for(
                _ollama_embed(text),
                timeout=_EMBED_TIMEOUT,
            )
            logger.debug(f"embed complete: vector_dim={len(result)}")
            return result
        except asyncio.TimeoutError:
            logger.error(f"embed timed out after {_EMBED_TIMEOUT}s — is Ollama running? (ollama serve)")
            raise
        except Exception as exc:
            logger.error(f"embed failed — is Ollama running? (ollama serve): {exc}", exc_info=True)
            raise


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
    return bool(GROQ_API_KEY)


def provider_info() -> dict:
    return {
        "llm_provider":   "groq",
        "llm_model":      GROQ_MODEL,
        "embed_provider": "ollama",
        "embed_model":    OLLAMA_EMBED_MODEL,
    }
