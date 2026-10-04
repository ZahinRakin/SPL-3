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

import groq

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
_LLM_TIMEOUT   = 45.0          # per attempt
_EMBED_TIMEOUT = 30.0

# ── retries (rate limits and transient server errors) ─────────────────────────
# Retrying happens here rather than inside the Groq SDK so that every attempt gets its
# own timeout and Groq's retry-after hint is respected. A slot of _LLM_SEM is held while
# waiting, which slows the whole burst down instead of firing more doomed requests.

_LLM_MAX_RETRIES  = 5
_RETRY_BASE_DELAY = 2.0        # seconds, doubled on each attempt
_RETRY_MAX_DELAY  = 30.0
_RETRYABLE = (groq.RateLimitError, groq.InternalServerError, groq.APIConnectionError)

# ── lazy singleton ────────────────────────────────────────────────────────────

_groq_client = None


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        logger.info(f"Initialising Groq client: model={GROQ_MODEL!r}")
        _groq_client = groq.Groq(api_key=GROQ_API_KEY, max_retries=0)   # generate() retries
    return _groq_client


# ── LLM: generate ─────────────────────────────────────────────────────────────

async def generate(
    prompt: str,
    json_mode: bool = False,
    temperature: float = 0.1,
) -> str:
    """Call Groq. Returns the raw text response. Retries rate limits and transient errors."""
    logger.debug(f"generate: json_mode={json_mode}, prompt_len={len(prompt)}")
    async with _LLM_SEM:
        for attempt in range(_LLM_MAX_RETRIES + 1):
            try:
                result = await asyncio.wait_for(
                    _groq_generate(prompt, json_mode, temperature),
                    timeout=_LLM_TIMEOUT,
                )
                logger.debug(f"generate complete: response_len={len(result)}")
                return result
            except _RETRYABLE as exc:
                if attempt == _LLM_MAX_RETRIES:
                    logger.error(f"generate failed after {attempt + 1} attempts: {exc}")
                    raise
                delay = _retry_delay(exc, attempt)
                logger.warning(
                    f"generate: {type(exc).__name__}, retrying in {delay:.1f}s "
                    f"(retry {attempt + 1}/{_LLM_MAX_RETRIES})"
                )
                await asyncio.sleep(delay)
            except asyncio.TimeoutError:
                logger.error(f"generate timed out after {_LLM_TIMEOUT}s")
                raise
            except Exception as exc:
                logger.error(f"generate failed: {exc}", exc_info=True)
                raise
    raise RuntimeError("unreachable: the retry loop always returns or raises")


def _retry_delay(exc: Exception, attempt: int) -> float:
    """Groq's retry-after header when it sends one, else exponential backoff."""
    backoff = _RETRY_BASE_DELAY * (2 ** attempt)
    response = getattr(exc, "response", None)
    header = response.headers.get("retry-after") if response is not None else None
    try:
        delay = float(header) if header else backoff
    except ValueError:
        delay = backoff
    return min(max(delay, 0.5), _RETRY_MAX_DELAY)


def _groq_generate_sync(prompt: str, json_mode: bool, temperature: float) -> str:
    # Errors are logged once, by generate(), which knows whether it will retry.
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


# ── Embeddings (Ollama) ───────────────────────────────────────────────────────

# nomic-embed-text was trained with task prefixes; without them, query and passage
# vectors match each other less well. Other embedding models get the text unchanged.
_NOMIC_TASK_PREFIX = {
    "retrieval_document": "search_document: ",
    "retrieval_query":    "search_query: ",
}


def _with_task_prefix(text: str, task_type: str) -> str:
    if not OLLAMA_EMBED_MODEL.startswith("nomic-embed-text"):
        return text
    prefix = _NOMIC_TASK_PREFIX.get(task_type)
    if prefix is None:
        logger.warning(f"embed: unknown task_type={task_type!r}, treating it as a document")
        prefix = _NOMIC_TASK_PREFIX["retrieval_document"]
    return prefix + text


async def embed(text: str, task_type: str = "retrieval_document") -> List[float]:
    """Embed text using Ollama. task_type: "retrieval_document" or "retrieval_query"."""
    logger.debug(f"embed: task_type={task_type}, text_len={len(text)}")
    async with _EMBED_SEM:
        try:
            result = await asyncio.wait_for(
                _ollama_embed(_with_task_prefix(text, task_type)),
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
