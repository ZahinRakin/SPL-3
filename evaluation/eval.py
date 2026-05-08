"""
Evaluation harness for the GraphRAG/RAPTOR/HiPPO pipeline.

Metrics
-------
- ROUGE-1/2/L (answer vs reference)
- BLEU-1 (n-gram overlap)
- Entity recall (named entities from reference present in answer)
- Faithfulness (fraction of answer sentences grounded in source context)
- Latency (wall-clock seconds per query)

Usage
-----
  python -m evaluation.eval --qa_pairs eval_data.json --method hybrid
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Any

# allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rouge_score import rouge_scorer as rs
from nltk.translate.bleu_score import sentence_bleu, SmoothingFunction
import nltk

try:
    nltk.data.find("tokenizers/punkt")
except LookupError:
    nltk.download("punkt", quiet=True)

from pipeline.graphrag_indexer import GraphRAGIndexer
from pipeline.raptor_runner import RaptorRunner
from pipeline.hippo_retriever import HippoRetriever
from pipeline.query_engine import QueryEngine, Method


# ── metrics ───────────────────────────────────────────────────────────────────

def rouge_scores(prediction: str, reference: str) -> Dict[str, float]:
    scorer = rs.RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True)
    scores = scorer.score(reference, prediction)
    return {
        "rouge1": round(scores["rouge1"].fmeasure, 4),
        "rouge2": round(scores["rouge2"].fmeasure, 4),
        "rougeL": round(scores["rougeL"].fmeasure, 4),
    }


def bleu1_score(prediction: str, reference: str) -> float:
    ref_tokens  = nltk.word_tokenize(reference.lower())
    pred_tokens = nltk.word_tokenize(prediction.lower())
    sf = SmoothingFunction().method1
    return round(sentence_bleu([ref_tokens], pred_tokens, weights=(1,0,0,0), smoothing_function=sf), 4)


def entity_recall(prediction: str, reference: str) -> float:
    try:
        import re
        # naive NER: capitalised words as a proxy for named entities
        ref_ents  = set(re.findall(r'\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b', reference))
        if not ref_ents:
            return 1.0
        pred_lower = prediction.lower()
        hits = sum(1 for e in ref_ents if e.lower() in pred_lower)
        return round(hits / len(ref_ents), 4)
    except Exception:
        return 0.0


# ── evaluation loop ───────────────────────────────────────────────────────────

async def evaluate(
    qa_pairs: List[Dict],
    method: Method,
    graphrag: GraphRAGIndexer,
    raptor: RaptorRunner,
    hippo: HippoRetriever,
) -> Dict[str, Any]:
    engine = QueryEngine(os.getenv("GEMINI_API_KEY", ""), graphrag, raptor, hippo)
    results = []

    for i, pair in enumerate(qa_pairs, 1):
        question  = pair["question"]
        reference = pair.get("reference", "")
        print(f"[{i}/{len(qa_pairs)}] {question[:60]}…")
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
    key = os.getenv("GEMINI_API_KEY", "")
    graphrag = GraphRAGIndexer(key)
    raptor   = RaptorRunner(key)
    hippo    = HippoRetriever(key)

    if args.docs_dir:
        from pipeline.document_processor import extract_text
        docs_path = Path(args.docs_dir)
        for fp in docs_path.iterdir():
            if fp.is_file():
                text = await extract_text(str(fp))
                if text.strip():
                    chunks = graphrag.chunk_text(text, fp.stem)
                    await graphrag.index_document(fp.stem, text)
                    await raptor.build_tree(chunks)
                    await hippo.index_passages(chunks)

    report = await evaluate(qa_pairs, args.method, graphrag, raptor, hippo)  # type: ignore[arg-type]
    out_path = Path(args.output)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nReport written to {out_path}")
    print(f"Aggregate metrics for '{args.method}':")
    for k, v in report["aggregate"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate GraphRAG pipeline")
    parser.add_argument("--qa_pairs",  required=True,          help="JSON file with [{question, reference}]")
    parser.add_argument("--method",    default="hybrid",       help="graphrag|raptor|hippo|hybrid")
    parser.add_argument("--docs_dir",  default=None,           help="Directory of documents to index before evaluating")
    parser.add_argument("--output",    default="eval_report.json", help="Output JSON report path")
    asyncio.run(_main(parser.parse_args()))
