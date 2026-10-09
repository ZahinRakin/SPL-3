"""
Build and save one index per dataset (EVALUATION_PLAN.md §8 step 3).

The same cascade as the app (backend/api/documents.py): chunk → RAPTOR tree per document →
GraphRAG extraction over the chunks. Differences, all for the evaluation's scale:
- Extraction runs in two passes. Pass 1 calls the LLM for every chunk concurrently (filling the
  response cache); pass 2 integrates documents one by one in corpus order, where every call is a
  cache hit. The graph therefore doesn't depend on which API call happened to finish first.
- RAPTOR trees are built concurrently, one RaptorRunner per document, then merged in corpus order.
- Communities: index_document(update_communities=False) per document, one update_communities()
  at the end (plan §8 step 3).
- MuSiQue / 2Wiki are not re-chunked (§3.1): each passage is one chunk, text = title + "\\n" + text.
- --per_doc builds a separate index for each document (GraphRAG-Bench: one knowledge base per novel).

Usage:
  python -m evaluation.harness.index --dataset musique [--limit 200 --name pilot]
"""
import argparse
import asyncio
import json
import random
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from backend.core.logger import logger
from backend.pipeline.graphrag_indexer import GraphRAGIndexer
from backend.pipeline.raptor_runner import RaptorRunner
from evaluation.harness.common import (
    LLM_CACHE_DIR, SEED, bench, key_usage, now, read_jsonl, run_log, settled_usage, setup,
)

SINGLE_PASSAGE = {"musique", "2wiki"}   # corpus items are already passages (§3.1)
_RAPTOR_CONCURRENCY = 8                 # documents whose trees are built at the same time
_PROGRESS_EVERY = 250


def cache_entries() -> int:
    return sum(1 for _ in LLM_CACHE_DIR.rglob("*.json")) if LLM_CACHE_DIR.exists() else 0


def chunks_for(dataset: str, doc: Dict, graphrag: GraphRAGIndexer) -> List[Dict]:
    if dataset in SINGLE_PASSAGE:
        return [{"id": f"{doc['doc_id']}_c0", "text": f"{doc['title']}\n{doc['text']}", "doc_id": doc["doc_id"]}]
    text = doc["text"] if dataset == "graphrag_bench_novel" else f"{doc['title']}\n{doc['text']}"
    return graphrag.chunk_text(text, doc["doc_id"])


# ── one index ─────────────────────────────────────────────────────────────────

async def build_index(dataset: str, docs: List[Dict], label: str) -> Tuple[GraphRAGIndexer, RaptorRunner, Dict]:
    graphrag, raptor = GraphRAGIndexer(), RaptorRunner()
    t0 = time.perf_counter()
    doc_chunks = [(d, chunks_for(dataset, d, graphrag)) for d in docs]
    n_chunks = sum(len(c) for _, c in doc_chunks)
    logger.info(f"[index {label}] {len(docs)} docs, {n_chunks} chunks")

    # Stage 1: RAPTOR, one runner per document, merged in corpus order.
    sem = asyncio.Semaphore(_RAPTOR_CONCURRENCY)
    done = 0

    async def tree(chunks: List[Dict]) -> RaptorRunner:
        nonlocal done
        async with sem:
            r = RaptorRunner()
            await r.build_tree(chunks)
            done += 1
            if done % _PROGRESS_EVERY == 0:
                logger.info(f"[index {label}] RAPTOR trees {done}/{len(doc_chunks)}")
            return r

    runners = await asyncio.gather(*[tree(c) for _, c in doc_chunks])
    for r in runners:
        raptor.nodes.update(r.nodes)
    raptor._index = None
    t_raptor = time.perf_counter() - t0

    # Passages for GraphRAG: each document's RAPTOR nodes (its chunks + summaries).
    passages = {d["doc_id"]: r.passages(d["doc_id"]) for (d, _), r in zip(doc_chunks, runners)}
    leaves = [p for ps in passages.values() for p in ps if p["level"] == 0]

    # Stage 2a: every extraction concurrently (fills the cache; results are discarded).
    t1 = time.perf_counter()
    finished = 0

    async def extract(p: Dict) -> int:
        nonlocal finished
        ents, rels = await graphrag._extract(p)
        finished += 1
        if finished % _PROGRESS_EVERY == 0:
            logger.info(f"[index {label}] extraction {finished}/{len(leaves)}")
        return int(not ents and not rels)

    empty = sum(await asyncio.gather(*[extract(p) for p in leaves]))
    t_extract = time.perf_counter() - t1

    # Stage 2b: integrate in corpus order (all cache hits), then communities once.
    for d, _ in doc_chunks:
        await graphrag.index_document(d["doc_id"], d["text"], passages[d["doc_id"]], update_communities=False)
    t2 = time.perf_counter()
    await graphrag.update_communities()
    t_comm = time.perf_counter() - t2

    stats = {
        "label": label, "docs": len(docs), "chunks": n_chunks,
        "raptor_levels": {str(k): v for k, v in raptor.get_stats()["levels"].items()},
        "summaries": sum(1 for n in raptor.nodes.values() if n.level > 0),
        "extractions_empty": empty,
        **graphrag.get_stats(),
        "isolated_entities": sum(1 for n in graphrag.graph if graphrag.graph.degree(n) == 0),
        "seconds": {"raptor": round(t_raptor, 1), "extraction": round(t_extract, 1),
                    "communities": round(t_comm, 1), "total": round(time.perf_counter() - t0, 1)},
    }
    logger.info(f"[index {label}] done: {stats}")
    return graphrag, raptor, stats


def save_index(out: Path, graphrag: GraphRAGIndexer, raptor: RaptorRunner, stats: Dict) -> None:
    """graph.json (GraphRAG state), raptor.json (nodes without embeddings), embeddings.npy."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "graph.json").write_text(json.dumps(graphrag.export_state()), encoding="utf-8")
    nodes = raptor.export_state()["nodes"]
    emb = np.array([n["embedding"] for n in nodes], dtype=np.float32)
    for n in nodes:
        n["embedding"] = None
    (out / "raptor.json").write_text(json.dumps({"nodes": nodes}), encoding="utf-8")
    np.save(out / "embeddings.npy", emb)
    (out / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")


def load_index(path: Path) -> Tuple[GraphRAGIndexer, RaptorRunner]:
    graphrag, raptor = GraphRAGIndexer(), RaptorRunner()
    graphrag.load_state(json.loads((path / "graph.json").read_text(encoding="utf-8")))
    state = json.loads((path / "raptor.json").read_text(encoding="utf-8"))
    emb = np.load(path / "embeddings.npy")
    for n, e in zip(state["nodes"], emb):
        n["embedding"] = e.astype(float).tolist()
    raptor.load_state(state)
    return graphrag, raptor


# ── main ──────────────────────────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> None:
    root = bench(args.dataset)
    log_file = root / "logs" / f"index_{args.name}_{time.strftime('%Y%m%d_%H%M%S')}.log"
    setup("index", log_file)
    docs = read_jsonl(root / "corpus.jsonl")
    if args.limit:
        docs = sorted(random.Random(SEED).sample(docs, args.limit), key=lambda d: docs.index(d))
    if args.docs:
        wanted = args.docs.split(",")
        by_id = {d["doc_id"]: d for d in docs}
        docs = [by_id[w] for w in wanted]
    usage0 = key_usage()["usage"]
    cache0 = cache_entries()
    run_log(f"index `{args.dataset}` / `{args.name}` started: {len(docs)} docs, key usage ${usage0:.4f}, "
            f"cache entries {cache0}, log `{log_file.relative_to(root.parent.parent)}`")
    out = root / "index" / args.name
    all_stats = []
    if args.per_doc:
        for d in docs:
            g, r, s = await build_index(args.dataset, [d], f"{args.name}/{d['doc_id']}")
            save_index(out / d["doc_id"], g, r, s)
            all_stats.append(s)
    else:
        g, r, s = await build_index(args.dataset, docs, args.name)
        save_index(out, g, r, s)
        all_stats.append(s)
    usage1 = settled_usage()
    cache1 = cache_entries()
    summary = {
        "dataset": args.dataset, "name": args.name, "docs": len(docs), "finished_at": now(),
        "cost_usd": round(usage1 - usage0, 4), "key_usage_before": usage0, "key_usage_after": usage1,
        "new_cache_entries": cache1 - cache0,
        "chunks": sum(s["chunks"] for s in all_stats), "summaries": sum(s["summaries"] for s in all_stats),
        "entities": sum(s["total_entities"] for s in all_stats),
        "relationships": sum(s["total_relationships"] for s in all_stats),
        "communities": sum(s["total_communities"] for s in all_stats),
        "seconds": round(sum(s["seconds"]["total"] for s in all_stats), 1),
        "per_index": all_stats,
    }
    (out / "build_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    run_log(f"index `{args.dataset}` / `{args.name}` finished: cost ${summary['cost_usd']:.4f}, "
            f"{summary['new_cache_entries']} new LLM responses, {summary['chunks']} chunks, "
            f"{summary['summaries']} summaries, {summary['entities']} entities, "
            f"{summary['relationships']} relationships, {summary['communities']} community summaries, "
            f"{summary['seconds']} s")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_index"}, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--name", default="full")
    ap.add_argument("--limit", type=int, default=0, help="index a seeded random sample of N documents")
    ap.add_argument("--per_doc", action="store_true", help="one index per document (GraphRAG-Bench)")
    ap.add_argument("--docs", default="", help="comma-separated document ids to index, in this order")
    asyncio.run(main(ap.parse_args()))
