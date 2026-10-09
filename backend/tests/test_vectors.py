"""The embedding fallback: random vectors in the app, a hard failure in strict (evaluation) mode."""
import asyncio

import pytest

import backend.pipeline.vectors as vectors


@pytest.fixture
def ollama_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*args, **kwargs):
        raise ConnectionError("ollama is not running")

    monkeypatch.setattr(vectors, "embed", fail)
    monkeypatch.setattr(vectors, "embed_many", fail)
    yield
    vectors.set_strict_embeddings(False)


def test_app_mode_falls_back_to_random_vectors(ollama_down):
    out = asyncio.run(vectors.embed_many_or_fallback(["a", "b"], owner="test"))
    assert len(out) == 2 and len(out[0]) == vectors.EMBED_DIM


def test_strict_mode_raises(ollama_down):
    vectors.set_strict_embeddings(True)
    with pytest.raises(ConnectionError):
        asyncio.run(vectors.embed_many_or_fallback(["a"], owner="test"))
    with pytest.raises(ConnectionError):
        asyncio.run(vectors.embed_or_fallback("a", owner="test"))
