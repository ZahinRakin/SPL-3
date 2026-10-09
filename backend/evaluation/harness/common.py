"""
Shared setup for every evaluation script (EVALUATION_PLAN.md §1).

- setup(): seeds (42), the on-disk LLM cache, strict embeddings, and a run log file.
- Paths for each benchmark, JSONL helpers.
- OpenRouter key usage (for the budget ledger); the key itself is never printed or logged.
"""
import hashlib
import json
import logging
import random
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from backend.core.config import settings
from backend.core.logger import logger
from backend.pipeline import llm_provider
from backend.pipeline.vectors import set_strict_embeddings

SEED = 42
EVAL_DIR = Path(__file__).resolve().parents[1]
BENCH_DIR = EVAL_DIR / "benchmarks"
LLM_CACHE_DIR = EVAL_DIR / "llm_cache"
RUN_LOG = EVAL_DIR / "RUN_LOG.md"
DATASETS = ["musique", "2wiki", "quality", "multihop_rag", "graphrag_bench_novel"]


def bench(name: str) -> Path:
    return BENCH_DIR / name


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ── setup ─────────────────────────────────────────────────────────────────────

def setup(script: str, log_file: Optional[Path] = None) -> None:
    """Rule 3 (cache + strict embeddings) and rule 7 (seed 42), and a per-run log file
    that captures everything the pipeline logs (DEBUG and up) for the evidence trail."""
    random.seed(SEED)
    np.random.seed(SEED)
    llm_provider.set_llm_cache(str(LLM_CACHE_DIR))
    set_strict_embeddings(True)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        # The console stays at INFO so long runs stay readable.
        for h in logger.handlers:
            if h is not handler and isinstance(h, logging.StreamHandler):
                h.setLevel(logging.INFO)
    logger.info(f"[{script}] start {now()} argv={sys.argv[1:]} model={llm_provider.LLM_MODEL!r} "
                f"provider_sort={settings.LLM_PROVIDER_SORT!r} cache={LLM_CACHE_DIR}")


def run_log(entry: str) -> None:
    """Append one timestamped line to backend/evaluation/RUN_LOG.md (the chronological record)."""
    with open(RUN_LOG, "a", encoding="utf-8") as f:
        f.write(f"- {now()} — {entry}\n")


# ── files ─────────────────────────────────────────────────────────────────────

def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_split(name: str, split: str) -> List[str]:
    return [l.strip() for l in open(bench(name) / "splits" / f"{split}.txt", encoding="utf-8") if l.strip()]


# ── OpenRouter usage (budget ledger) ──────────────────────────────────────────

def key_usage() -> Dict[str, float]:
    """Cumulative spend on the OpenRouter key, in USD. OpenRouter updates it with a short delay."""
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
    )
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())["data"]
    return {"usage": float(data["usage"]), "limit": float(data.get("limit") or 0),
            "limit_remaining": float(data.get("limit_remaining") or 0)}


def settled_usage(min_wait_s: float = 150.0, max_wait_s: float = 900.0, poll_s: float = 30.0) -> float:
    """Key usage once billing has caught up. OpenRouter adds a step's cost to the counter a minute
    or more after the last call (the pilot showed no change after 60 s), so wait at least
    min_wait_s, then until two readings poll_s apart agree."""
    start = time.time()
    time.sleep(min_wait_s)
    last = key_usage()["usage"]
    while time.time() - start < max_wait_s:
        time.sleep(poll_s)
        cur = key_usage()["usage"]
        if abs(cur - last) < 1e-9:
            return cur
        last = cur
    return last
