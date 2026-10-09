"""
Shared fixtures. The pipeline's LLM and embedding calls are replaced with fast, deterministic
stubs, so the tests run offline: no Ollama, no OpenRouter, no database.
"""
import hashlib
from typing import Dict, List

import numpy as np
import pytest

import backend.pipeline.query_engine as query_engine
import backend.pipeline.raptor_runner as raptor_runner
from backend.pipeline.vectors import EMBED_DIM


def fake_vector(text: str) -> List[float]:
    """A stable pseudo-random unit vector per text (hash() is salted per process; md5 is not)."""
    seed = int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)
    v = np.random.default_rng(seed).standard_normal(EMBED_DIM)
    return (v / np.linalg.norm(v)).tolist()


class FakeModels:
    """Records every prompt; answers summaries with a fixed text and queries with JSON."""

    def __init__(self) -> None:
        self.prompts: List[str] = []
        self.vectors: Dict[str, List[float]] = {}   # text → vector override

    def vector(self, text: str) -> List[float]:
        return self.vectors.get(text) or fake_vector(text)

    async def generate(self, prompt: str, json_mode: bool = False, temperature: float = 0.1) -> str:
        self.prompts.append(prompt)
        if json_mode:
            return '{"answer": "stub", "key_entities": [], "confidence": 0.5, "reasoning": ""}'
        return f"summary {len(self.prompts)}"

    async def embed_many(self, texts: List[str], owner: str = "", task_type: str = "") -> List[List[float]]:
        return [self.vector(t) for t in texts]

    async def embed_one(self, text: str, owner: str = "", task_type: str = "") -> List[float]:
        return self.vector(text)


@pytest.fixture
def models(monkeypatch: pytest.MonkeyPatch) -> FakeModels:
    fake = FakeModels()
    monkeypatch.setattr(raptor_runner, "generate", fake.generate)
    monkeypatch.setattr(raptor_runner, "embed_many_or_fallback", fake.embed_many)
    monkeypatch.setattr(query_engine, "generate", fake.generate)
    monkeypatch.setattr(query_engine, "embed_many_or_fallback", fake.embed_many)
    monkeypatch.setattr(query_engine, "embed_or_fallback", fake.embed_one)
    return fake
