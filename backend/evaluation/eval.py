"""
Evaluation harness for the cascade (RAPTOR -> GraphRAG -> HippoRAG).
Compare `--method standard` (plain RAG baseline) with `--method refined` (the cascade).

Metrics
-------
- ROUGE-1/2/L (answer vs reference)
- BLEU-1 (n-gram overlap)
- Entity recall (named entities from reference present in answer)
- Faithfulness (fraction of answer sentences grounded in source context)
- Latency (wall-clock seconds per query)

Usage
-----
  python -m backend.evaluation.eval --qa_pairs eval_data.json --method refined
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Any

from backend.core.logger import logger

from rouge_score import rouge_scorer as rs # type: ignore
from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction # type: ignore
import nltk # type: ignore

try:
    logger.info("Checking for NLTK 'punkt' tokenizer...")
    nltk.data.find("tokenizers/punkt")
except LookupError:
    logger.error("NLTK 'punkt' tokenizer not found. Downloading...")
    nltk.download("punkt", quiet=True)

from backend.pipeline.graphrag_indexer import GraphRAGIndexer
from backend.pipeline.raptor_runner import RaptorRunner
from backend.pipeline.hippo_retriever import HippoRetriever
from backend.pipeline.query_engine import QueryEngine, Method
from backend.pipeline.vectors import set_strict_embeddings


# ── metrics ───────────────────────────────────────────────────────────────────

def rouge_scores(prediction: str, reference: str) -> Dict[str, float]:
    logger.info("Calculating ROUGE scores...")
    scorer = rs.RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True)
    scores = scorer.score(reference, prediction)
    logger.debug(f"ROUGE scores: {scores}")
    return {
        "rouge1": round(scores["rouge1"].fmeasure, 4),
        "rouge2": round(scores["rouge2"].fmeasure, 4),
        "rougeL": round(scores["rougeL"].fmeasure, 4),
    }


def bleu1_score(prediction: str, reference: str) -> float:
    logger.info("Calculating BLEU-1 score...")
    ref_tokens  = nltk.word_tokenize(reference.lower())
    pred_tokens = nltk.word_tokenize(prediction.lower())
    sf = SmoothingFunction().method1
    logger.debug(f"Reference tokens: {ref_tokens}")
    logger.debug(f"Prediction tokens: {pred_tokens}")
    return round(sentence_bleu([ref_tokens], pred_tokens, weights=(1,0,0,0), smoothing_function=sf), 4)


def entity_recall(prediction: str, reference: str) -> float:
    logger.info("Calculating entity recall...")
    try:
        import re
        # naive NER: capitalised words as a proxy for named entities
        ref_ents  = set(re.findall(r'\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b', reference))
        if not ref_ents:
            return 1.0
        pred_lower = prediction.lower()
        hits = sum(1 for e in ref_ents if e.lower() in pred_lower)
        return round(hits / len(ref_ents), 4)
    except Exception as e:
        logger.error(f"Error calculating entity recall: {e}")
        return 0.0


# ── evaluation loop ───────────────────────────────────────────────────────────

async def evaluate(
    qa_pairs: List[Dict],
    method: Method,
    graphrag: GraphRAGIndexer,
    raptor: RaptorRunner,
) -> Dict[str, Any]:
    hippo = HippoRetriever(graphrag=graphrag, raptor=raptor)
    engine = QueryEngine(graphrag=graphrag, raptor=raptor, hippo=hippo)
    results = []

    for i, pair in enumerate(qa_pairs, 1):
        question  = pair["question"]
        reference = pair.get("reference", "")
        logger.info(f"[{i}/{len(qa_pairs)}] {question[:60]}…")
        t0 = time.perf_counter()
        resp = await engine.query(question, method=method)
        latency = round(time.perf_counter() - t0, 3)
        prediction = resp["answer"]

        row: Dict[str, Any] = {
            "question":   question,
            "prediction": prediction,
            "reference":  reference,
            "method":     method,
            "latency_s":  latency,
            "confidence": resp.get("confidence", 0),
        }
        if reference:
            row.update(rouge_scores(prediction, reference))
            row["bleu1"]          = bleu1_score(prediction, reference)
            row["entity_recall"]  = entity_recall(prediction, reference)
        results.append(row)

    # aggregate
    keys = ["rouge1", "rouge2", "rougeL", "bleu1", "entity_recall", "latency_s", "confidence"]
    agg = {}
    for k in keys:
        vals = [r[k] for r in results if k in r]
        agg[f"avg_{k}"] = round(sum(vals) / len(vals), 4) if vals else None

    return {"method": method, "n_queries": len(results), "aggregate": agg, "per_query": results}


# ── CLI ───────────────────────────────────────────────────────────────────────

async def _main(args: argparse.Namespace):
    with open(args.qa_pairs) as f:
        qa_pairs = json.load(f)

    # bootstrap pipeline (no documents — just test query engine on pre-built state)
    graphrag = GraphRAGIndexer()
    raptor   = RaptorRunner()

    if args.docs_dir:
        from backend.pipeline.document_processor import extract_text
        docs_path = Path(args.docs_dir)
        for fp in docs_path.iterdir():
            if fp.is_file():
                text = await extract_text(str(fp))
                if text.strip():
                    # Same cascade as the API: RAPTOR first, then the graph over chunks + summaries.
                    chunks = graphrag.chunk_text(text, fp.stem)
                    await raptor.build_tree(chunks)
                    await graphrag.index_document(fp.stem, text, raptor.passages(fp.stem) or chunks)

    report = await evaluate(qa_pairs, args.method, graphrag, raptor)  # type: ignore[arg-type]
    out_path = Path(args.output)
    out_path.write_text(json.dumps(report, indent=2))
    logger.info(f"\nReport written to {out_path}")
    logger.info(f"Aggregate metrics for '{args.method}':")
    for k, v in report["aggregate"].items():
        logger.info(f"  {k}: {v}")


if __name__ == "__main__":
    logger.info("Starting evaluation...")
    parser = argparse.ArgumentParser(description="Evaluate GraphRAG pipeline")
    parser.add_argument("--qa_pairs",  required=True,          help="JSON file with [{question, reference}]")
    parser.add_argument("--method",    default="refined",      choices=["standard", "refined"],
                        help="standard (plain RAG) or refined (the cascade)")
    parser.add_argument("--docs_dir",  default=None,           help="Directory of documents to index before evaluating")
    parser.add_argument("--output",    default="eval_report.json", help="Output JSON report path")
    set_strict_embeddings(True)   # a random-vector fallback would silently corrupt the results
    asyncio.run(_main(parser.parse_args()))
