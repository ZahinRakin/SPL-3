"""
Unified LLM + embedding provider.

LLM:        any OpenAI-compatible API through the `openai` package. Default: OpenRouter.
            Configure LLM_API_KEY, LLM_BASE_URL and LLM_MODEL in .env.
Embeddings: Ollama (configure OLLAMA_BASE_URL and OLLAMA_EMBED_MODEL in .env)
            Run:  ollama pull nomic-embed-text && ollama serve

Response cache (off by default): set_llm_cache(dir) stores every successful LLM response
on disk, keyed by a hash of the request. Evaluation runs turn it on so that a crash, a
re-run or a new query-time ablation never pays for the same call twice.
"""
import asyncio
import hashlib
import json as _json
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import openai

from backend.core.config import settings
from backend.core.logger import logger

# ── config ────────────────────────────────────────────────────────────────────

LLM_API_KEY  = settings.LLM_API_KEY
LLM_BASE_URL = settings.LLM_BASE_URL
LLM_MODEL    = settings.LLM_MODEL
LLM_PROVIDER = urlparse(LLM_BASE_URL).hostname or LLM_BASE_URL     # e.g. "openrouter.ai"
_IS_OPENROUTER = LLM_PROVIDER.endswith("openrouter.ai")

# On Windows "localhost" tries IPv6 (::1) first; Ollama listens on IPv4 only, so every
# request waited ~2 s for the fallback (measured 2.08 s vs 0.05 s). Use the IPv4 address.
OLLAMA_BASE_URL    = settings.OLLAMA_BASE_URL.replace("//localhost", "//127.0.0.1")
OLLAMA_EMBED_MODEL = settings.OLLAMA_EMBED_MODEL

logger.info(
    f"LLM: {LLM_PROVIDER} model={LLM_MODEL!r} | "
    f"Embeddings: Ollama model={OLLAMA_EMBED_MODEL!r} at {OLLAMA_BASE_URL}"
)

# ── concurrency + timeout ─────────────────────────────────────────────────────

_LLM_SEM       = asyncio.Semaphore(settings.LLM_MAX_CONCURRENCY)
_EMBED_SEM     = asyncio.Semaphore(2)     # batch requests in flight; Ollama queues the rest anyway
_LLM_TIMEOUT   = 45.0          # per attempt
_EMBED_TIMEOUT = 60.0          # per batch request
_EMBED_BATCH_SIZE = 32
# nomic-embed-text's context is 2,048 *tokens* (~4 chars each). The cut is in characters,
# so 6,000 chars (~1,500 tokens) keeps whole chunks and summaries with room for the prefix.
_EMBED_MAX_CHARS  = 6000

# ── retries (rate limits and transient server errors) ─────────────────────────
# Retrying happens here rather than inside the SDK so that every attempt gets its own
# timeout and the provider's retry-after hint is respected. A slot of _LLM_SEM is held
# while waiting, which slows the whole burst down instead of firing more doomed requests.

_LLM_MAX_RETRIES  = 5
_RETRY_BASE_DELAY = 2.0        # seconds, doubled on each attempt
_RETRY_MAX_DELAY  = 30.0
_RETRYABLE = (openai.RateLimitError, openai.InternalServerError, openai.APIConnectionError)

# ── reasoning models ──────────────────────────────────────────────────────────
# gpt-oss models "think" before answering. At the default (medium) effort they often spend
# the whole output on hidden reasoning and return empty content, which fails JSON mode.
# Low effort fixes that and uses fewer tokens.
_REASONING_EFFORT = "low"

# Output cap per call. For reasoning models the hidden reasoning counts against it too, so it
# can't be small; it exists so one runaway generation can't spend a large part of the budget.
_DEFAULT_MAX_TOKENS = 4096

# ── response cache ────────────────────────────────────────────────────────────

_cache_dir: Optional[Path] = None


def set_llm_cache(directory: Optional[str]) -> None:
    """Turn the on-disk response cache on (a directory path) or off (None)."""
    global _cache_dir
    _cache_dir = Path(directory) if directory else None
    if _cache_dir is not None:
        _cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"LLM response cache: {_cache_dir}")


def _cache_key(prompt: str, json_mode: bool, temperature: float, max_tokens: Optional[int]) -> str:
    request = _json.dumps(
        {"model": LLM_MODEL, "effort": _REASONING_EFFORT, "prompt": prompt,
         "json": json_mode, "temperature": temperature, "max_tokens": max_tokens},
        sort_keys=True,
    )
    return hashlib.sha256(request.encode()).hexdigest()


def _cache_path(key: str) -> Path:
    return _cache_dir / key[:2] / f"{key}.json"   # type: ignore[operator]


def _cache_read(key: str) -> Optional[str]:
    try:
        return _json.loads(_cache_path(key).read_text(encoding="utf-8"))["response"]
    except (OSError, ValueError, KeyError):
        return None


def _cache_write(key: str, response: str) -> None:
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_json.dumps({"response": response}), encoding="utf-8")
    tmp.replace(path)   # atomic, so a crash never leaves a half-written entry


def _is_reasoning_model(model: str) -> bool:
    return "gpt-oss" in model


def _provider_options() -> Dict[str, Any]:
    """Per-provider request options for reasoning effort and routing."""
    if _IS_OPENROUTER:
        provider: Dict[str, Any] = {
            # Only route to providers that support every parameter we send (JSON mode,
            # reasoning effort); otherwise a provider may silently ignore them.
            "require_parameters": True,
        }
        if settings.LLM_PROVIDER_SORT:
            # By default OpenRouter prefers the cheapest host, which for gpt-oss-20b ran at
            # 18-44 tok/s (34-107 s per extraction). "throughput" picks the fastest host
            # (~400-700 tok/s, ~2.5 s) for a few tenths of a cent more per call.
            provider["sort"] = settings.LLM_PROVIDER_SORT
        extra_body: Dict[str, Any] = {"provider": provider}
        if _is_reasoning_model(LLM_MODEL):
            extra_body["reasoning"] = {"effort": _REASONING_EFFORT, "exclude": True}
        return {"extra_body": extra_body}
    if _is_reasoning_model(LLM_MODEL):
        return {"reasoning_effort": _REASONING_EFFORT}
    return {}


# ── lazy singleton ────────────────────────────────────────────────────────────

_llm_client: Optional[openai.AsyncOpenAI] = None


def _get_llm_client() -> openai.AsyncOpenAI:
    global _llm_client
    if _llm_client is None:
        logger.info(f"Initialising LLM client: {LLM_PROVIDER} model={LLM_MODEL!r}")
        headers = {"X-Title": "GraphRAG Investigations"} if _IS_OPENROUTER else None
        _llm_client = openai.AsyncOpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            max_retries=0,          # generate() retries
            default_headers=headers,
        )
    return _llm_client


# ── LLM: generate ─────────────────────────────────────────────────────────────

async def generate(
    prompt: str,
    json_mode: bool = False,
    temperature: float = 0.1,
    max_tokens: Optional[int] = _DEFAULT_MAX_TOKENS,
) -> str:
    """Call the LLM. Returns the raw text response. Retries rate limits and transient errors.
    With the cache on, a request seen before is answered from disk without an API call."""
    logger.debug(f"generate: json_mode={json_mode}, prompt_len={len(prompt)}")
    key = _cache_key(prompt, json_mode, temperature, max_tokens) if _cache_dir else None
    if key is not None:
        cached = await asyncio.to_thread(_cache_read, key)
        if cached is not None:
            logger.debug("generate: cache hit")
            return cached
    async with _LLM_SEM:
        for attempt in range(_LLM_MAX_RETRIES + 1):
            try:
                result = await asyncio.wait_for(
                    _chat_completion(prompt, json_mode, temperature, max_tokens),
                    timeout=_LLM_TIMEOUT,
                )
                logger.debug(f"generate complete: response_len={len(result)}")
                # Empty responses are not cached: they are failures, and a retry may succeed.
                if key is not None and result.strip():
                    await asyncio.to_thread(_cache_write, key, result)
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
    """The provider's retry-after header when it sends one, else exponential backoff."""
    backoff = _RETRY_BASE_DELAY * (2 ** attempt)
    response = getattr(exc, "response", None)
    header = response.headers.get("retry-after") if response is not None else None
    try:
        delay = float(header) if header else backoff
    except ValueError:
        delay = backoff
    return min(max(delay, 0.5), _RETRY_MAX_DELAY)


async def _chat_completion(
    prompt: str, json_mode: bool, temperature: float, max_tokens: Optional[int]
) -> str:
    # Errors are logged once, by generate(), which knows whether it will retry.
    kwargs: Dict[str, Any] = dict(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        **_provider_options(),
    )
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = await _get_llm_client().chat.completions.create(**kwargs)
    if not resp.choices:
        # OpenRouter can report an upstream failure inside a 200 response.
        raise openai.APIConnectionError(
            message=f"LLM returned no choices: {getattr(resp, 'error', None)}",
            request=None,  # type: ignore[arg-type]
        )
    choice = resp.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        logger.warning(f"LLM output hit max_tokens={max_tokens}; the response is cut off")
    return choice.message.content or ""


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
    """Embed one text. task_type: "retrieval_document" or "retrieval_query"."""
    return (await embed_many([text], task_type))[0]


async def embed_many(texts: List[str], task_type: str = "retrieval_document") -> List[List[float]]:
    """Embed many texts with Ollama's batch endpoint, _EMBED_BATCH_SIZE texts per request.
    One request per batch is ~16x faster than one request per text, because Ollama
    queues single requests. Returned vectors are L2-normalised."""
    if not texts:
        return []
    logger.debug(f"embed_many: task_type={task_type}, texts={len(texts)}")
    prefixed = [_with_task_prefix(t, task_type) for t in texts]
    truncated = sum(1 for t in prefixed if len(t) > _EMBED_MAX_CHARS)
    if truncated:
        logger.warning(f"embed_many: {truncated}/{len(texts)} texts cut to {_EMBED_MAX_CHARS} chars")
    prepared = [t[:_EMBED_MAX_CHARS] for t in prefixed]
    vectors: List[List[float]] = []
    for i in range(0, len(prepared), _EMBED_BATCH_SIZE):
        batch = prepared[i:i + _EMBED_BATCH_SIZE]
        async with _EMBED_SEM:
            try:
                vectors.extend(await asyncio.wait_for(_ollama_embed(batch), timeout=_EMBED_TIMEOUT))
            except asyncio.TimeoutError:
                logger.error(f"embed timed out after {_EMBED_TIMEOUT}s — is Ollama running? (ollama serve)")
                raise
            except Exception as exc:
                logger.error(f"embed failed — is Ollama running? (ollama serve): {exc}", exc_info=True)
                raise
    logger.debug(f"embed_many complete: {len(vectors)} vectors")
    return vectors


def _ollama_embed_sync(texts: List[str]) -> List[List[float]]:
    payload = _json.dumps({"model": OLLAMA_EMBED_MODEL, "input": texts}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=_EMBED_TIMEOUT) as resp:
        vectors = _json.loads(resp.read())["embeddings"]
    if len(vectors) != len(texts):
        raise ValueError(f"Ollama returned {len(vectors)} embeddings for {len(texts)} texts")
    return vectors


async def _ollama_embed(texts: List[str]) -> List[List[float]]:
    return await asyncio.to_thread(_ollama_embed_sync, texts)


# ── helpers for app.py health check ──────────────────────────────────────────

def active_api_key_set() -> bool:
    return bool(LLM_API_KEY)


def provider_info() -> dict:
    return {
        "llm_provider":   LLM_PROVIDER,
        "llm_model":      LLM_MODEL,
        "embed_provider": "ollama",
        "embed_model":    OLLAMA_EMBED_MODEL,
    }