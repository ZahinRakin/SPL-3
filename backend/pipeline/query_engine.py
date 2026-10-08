"""
Query Engine: answers questions over a case's indexed documents in one of two modes.

- standard: plain RAG, the baseline. The top-k chunks most similar to the question.
- refined:  the cascade. RAPTOR summaries enrich the passages, GraphRAG links them through
            one knowledge graph, and HippoRAG's Personalized PageRank picks the top-k.
"""
import json
from typing import Dict, List, Literal, Tuple

from backend.core.logger import logger
from .graphrag_indexer import GraphRAGIndexer
from .hippo_retriever import HippoRetriever
from .llm_provider import generate
from .raptor_runner import RaptorRunner
from .vectors import embed_or_fallback

Method = Literal["standard", "refined"]

_ANSWER_PROMPT = """You are an intelligent document-analysis assistant.
Answer the question ONLY from the provided context. If the context does not
contain enough information, say so clearly and briefly.

=== CONTEXT ===
{context}
=== END CONTEXT ===

{history}

QUESTION: {question}

Reply in this exact JSON format (no markdown fences):
{{
  "answer": "your detailed answer",
  "key_entities": ["entity1", "entity2"],
  "confidence": 0.85,
  "reasoning": "brief chain-of-thought"
}}"""


class QueryEngine:
    def __init__(
        self,
        api_key: str = "",
        graphrag: GraphRAGIndexer = None,
        raptor: RaptorRunner = None,
        hippo: HippoRetriever = None,
    ):
        self.graphrag = graphrag
        self.raptor = raptor
        self.hippo = hippo
        self.history: List[Dict] = []

    # ── embedding ─────────────────────────────────────────────────────────────

    async def _embed_query(self, query: str) -> List[float]:
        return await embed_or_fallback(query, owner="QueryEngine", task_type="retrieval_query")

    # ── context assembly ──────────────────────────────────────────────────────

    async def _build_context(
        self, question: str, query_emb: List[float], method: Method, top_k: int
    ) -> Tuple[str, List[str]]:
        blocks: List[str] = []
        sources: List[str] = []

        if method == "standard":
            passages = self.raptor.retrieve(query_emb, top_k=top_k, level=0)
        else:
            ranked = await self.hippo.retrieve(question, query_emb, top_k=top_k)
            passages = ranked["passages"]
            facts = self.graphrag.describe_entities(ranked["entities"])
            if facts:
                blocks.append(f"[KNOWLEDGE GRAPH]\n{facts}")

        for p in passages:
            label = "PASSAGE" if p["level"] == 0 else f"SUMMARY — level {p['level']}"
            blocks.append(f"[{label}]\n{p['text']}")
            sources.extend(p.get("doc_ids", []))

        context = "\n\n".join(blocks)
        logger.debug(
            f"_build_context: method={method!r}, passages={len(passages)}, "
            f"context_len={len(context)}, sources={sources}"
        )
        return context, list(dict.fromkeys(sources))

    # ── history ───────────────────────────────────────────────────────────────

    def _history_str(self) -> str:
        if not self.history:
            return ""
        tail = self.history[-3:]
        turns = "\n".join(
            f"Q: {h['question']}\nA: {h['answer'][:200]}…" for h in tail
        )
        return f"=== CONVERSATION HISTORY ===\n{turns}\n=== END HISTORY ===\n"

    # ── query ─────────────────────────────────────────────────────────────────

    async def query(
        self, question: str, method: Method = "refined", top_k: int = 6
    ) -> Dict:
        logger.info(f"QueryEngine.query: method={method!r}, top_k={top_k}, question={question[:80]!r}")
        query_emb = await self._embed_query(question)
        context, sources = await self._build_context(question, query_emb, method, top_k)

        prompt = _ANSWER_PROMPT.format(
            context=context,
            history=self._history_str(),
            question=question,
        )

        try:
            raw = await generate(prompt, json_mode=True, temperature=0.3)
            data = json.loads(raw)
            logger.debug(f"QueryEngine.query: LLM response parsed, confidence={data.get('confidence')}")
        except json.JSONDecodeError as exc:
            logger.error(f"QueryEngine.query: JSON decode error from LLM response: {exc}", exc_info=True)
            data = {
                "answer": f"Error parsing LLM response: {exc}",
                "key_entities": [],
                "confidence": 0.0,
                "reasoning": "",
            }
        except Exception as exc:
            logger.error(f"QueryEngine.query: LLM generate failed: {exc}", exc_info=True)
            data = {
                "answer": f"Error generating answer: {exc}",
                "key_entities": [],
                "confidence": 0.0,
                "reasoning": "",
            }

        entities = []
        for name in data.get("key_entities", []):
            key = name.lower()
            for eid, ent in self.graphrag.entities.items():
                if ent.name.lower() == key:
                    entities.append({"id": eid, "name": ent.name, "type": ent.type.lower()})
                    break

        result = {
            "answer": data.get("answer", ""),
            "entities": entities,
            "sources": sources,
            "confidence": float(data.get("confidence", 0.7)),
            "reasoning": data.get("reasoning", ""),
            "method": method,
        }
        self.history.append({"question": question, "answer": result["answer"]})
        logger.info(
            f"QueryEngine.query complete: confidence={result['confidence']}, "
            f"entities={len(entities)}, sources={len(sources)}"
        )
        return result

    # ── suggestions ───────────────────────────────────────────────────────────

    def get_suggestions(self) -> List[str]:
        suggestions = [
            "Summarise the key themes across all documents",
            "What relationships exist between the main entities?",
            "What are the most important facts mentioned?",
            "Are there any contradictions or tensions in the documents?",
            "Which entities appear most frequently?",
        ]
        type_counts: Dict[str, int] = {}
        for ent in self.graphrag.entities.values():
            type_counts[ent.type] = type_counts.get(ent.type, 0) + 1
        if type_counts:
            top_type = max(type_counts, key=type_counts.get)  # type: ignore[arg-type]
            suggestions.insert(0, f"Who are the main {top_type.lower()} entities?")
        return suggestions[:5]

    # ── state (persistence) ───────────────────────────────────────────────────
    # Only the conversation history; the retrievers persist their own state.

    def export_state(self) -> Dict:
        return {"history": list(self.history)}

    def load_state(self, state: Dict) -> None:
        self.history = list(state.get("history", []))
