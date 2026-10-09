"""
Query Engine: answers questions over a case's indexed documents in one of two modes.

- standard: plain RAG, the baseline. The top-k chunks most similar to the question.
- refined:  the cascade. RAPTOR summaries enrich the passages, GraphRAG links them through
            one knowledge graph, and HippoRAG's Personalized PageRank picks the top-k.
            GraphRAG's community summaries are added too: a few for a specific question,
            more for a broad one ("overall", "themes", "across all documents"), which is
            where the GraphRAG paper finds them most useful.

Fair comparison: by default (context_budget=None) each method takes top_k passages, and
refined also adds graph facts and community summaries ("graph extras"), so it reads more.
With context_budget set (characters, ~4 per token), both methods fill the same budget:
refined's extras count against it, and passages fill the rest in rank order.
graph_extras=False drops the extras, for ablations.

Block order: passages first (best first), then community summaries, then graph facts.
Models use the start of the context most reliably, and the passages hold the facts.
"""
import json
import re
from typing import Dict, List, Literal, Optional, Tuple

from backend.core.logger import logger
from .graphrag_indexer import GraphRAGIndexer
from .hippo_retriever import HippoRetriever
from .llm_provider import generate
from .raptor_runner import RaptorRunner
from .vectors import EmbeddingIndex, embed_many_or_fallback, embed_or_fallback

Method = Literal["standard", "refined"]

# Broad, collection-wide questions (keyword cues, like entity matching in D8: no extra LLM call).
_GLOBAL_CUES = re.compile(
    r"\b(overall|overview|summar(y|ies|ise|ize)|themes?|across|in general|patterns?|trends?"
    r"|all (the )?(documents|files|evidence|cases|records)"
    r"|(main|key|common) (issues|points|topics|findings|themes|problems|risks))\b",
    re.IGNORECASE,
)
_LOCAL_COMMUNITIES = 2       # community summaries in the context for a specific question
_GLOBAL_COMMUNITIES = 6      # ...and for a broad one
_COMMUNITY_PPR_WEIGHT = 0.5  # like passages: cosine + λ · PageRank mass normalised to the best
_BUDGET_POOL = 40            # passages ranked when filling a context budget
_MAX_EXTRAS_SHARE = 0.35     # with a budget, graph extras may use at most this share of it
_COMMUNITY_LABEL_DOCS = 3    # documents named in a community block's source label

# Multi-hop questions need facts from several sources combined, and they rarely say so; the
# prompt asks the model to keep sources apart only where they are about different things.
_ANSWER_PROMPT = """You are an intelligent document-analysis assistant.
Answer the question ONLY from the provided context. If the context does not
contain enough information, say so clearly and briefly. Each block names its source
document. Keep track of which source each fact comes from, and combine facts from
different sources only when they clearly refer to the same entity or event.

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
        # doc id → readable name for the source labels. The evaluation's doc ids are already
        # file names; the app's are UUIDs, so the case registry fills this from `documents`.
        self.doc_names: Dict[str, str] = {}
        self._community_index: Optional[EmbeddingIndex] = None
        self._community_index_key: Optional[int] = None

    # ── embedding ─────────────────────────────────────────────────────────────

    async def _embed_query(self, query: str) -> List[float]:
        return await embed_or_fallback(query, owner="QueryEngine", task_type="retrieval_query")

    async def _community_embeddings(self) -> EmbeddingIndex:
        """Community summaries embedded once and cached until the summaries change
        (they are rewritten after each upload, and they aren't stored with embeddings)."""
        summaries = self.graphrag.community_summaries
        key = hash(frozenset(summaries.items()))
        if self._community_index is None or key != self._community_index_key:
            ids = [str(cid) for cid in summaries]
            vectors = await embed_many_or_fallback(list(summaries.values()), owner="QueryEngine")
            self._community_index = EmbeddingIndex(ids, vectors)
            self._community_index_key = key
            logger.debug(f"QueryEngine: embedded {len(ids)} community summaries")
        return self._community_index

    # ── context assembly ──────────────────────────────────────────────────────

    def _source_label(self, doc_ids: List[str]) -> str:
        return ", ".join(self.doc_names.get(d, d) for d in doc_ids) or "unknown"

    async def _rank_communities(self, query_emb: List[float], ppr_mass: Dict[int, float]) -> List[int]:
        """Every summarised community, best first."""
        if not self.graphrag.community_summaries:
            return []
        ids, sims = (await self._community_embeddings()).cosine(query_emb)
        best = max(ppr_mass.values(), default=0.0)
        scored = sorted(
            (
                (float(sim) + (_COMMUNITY_PPR_WEIGHT * ppr_mass.get(int(cid), 0.0) / best if best > 0 else 0.0),
                int(cid))
                for cid, sim in zip(ids, sims)
            ),
            reverse=True,
        )
        return [cid for _, cid in scored]

    @staticmethod
    def _select_communities(
        ranked: List[int], docs: Dict[int, List[str]], limit: int, cover_documents: bool
    ) -> List[int]:
        """The top `limit` communities. For a broad question, each document's best community
        comes first: otherwise the document with the most communities fills every slot."""
        chosen: List[int] = []
        if cover_documents:
            covered: set = set()
            for cid in ranked:
                if len(chosen) >= limit:
                    break
                if set(docs.get(cid, [])) - covered:
                    chosen.append(cid)
                    covered.update(docs.get(cid, []))
        chosen += [cid for cid in ranked if cid not in chosen][: limit - len(chosen)]
        return sorted(chosen, key=ranked.index)

    def _community_docs(self, members: List[str]) -> List[str]:
        """The documents a community's entities were extracted from, most-mentioned first."""
        counts: Dict[str, int] = {}
        for eid in members:
            for pid in self.graphrag.entities[eid].source_chunks:
                node = self.raptor.nodes.get(pid)
                if node is not None:
                    for d in node.doc_ids:
                        counts[d] = counts.get(d, 0) + 1
        return sorted(counts, key=lambda d: -counts[d])

    def _community_label(self, docs: List[str]) -> str:
        # A community in a large collection can span thousands of documents; naming them all made
        # the block too big for any context budget, so the label names the main few.
        shown = self._source_label(docs[:_COMMUNITY_LABEL_DOCS])
        more = len(docs) - _COMMUNITY_LABEL_DOCS
        return shown + (f" (+{more} more)" if more > 0 else "")

    async def _graph_extras(
        self, question: str, query_emb: List[float], ranked: Dict
    ) -> Tuple[List[str], List[str], Dict[str, List[str]]]:
        """Refined's extra blocks: community summaries and graph facts, and each community
        block's source documents."""
        community_blocks: List[str] = []
        block_docs: Dict[str, List[str]] = {}
        is_global = bool(_GLOBAL_CUES.search(question))
        limit = _GLOBAL_COMMUNITIES if is_global else _LOCAL_COMMUNITIES
        members = self.graphrag.community_members()
        community_docs = {cid: self._community_docs(ids) for cid, ids in members.items()}
        ranked_communities = await self._rank_communities(query_emb, ranked.get("communities", {}))
        for cid in self._select_communities(ranked_communities, community_docs, limit, is_global):
            docs = community_docs.get(cid, [])
            block = (
                f"[COMMUNITY SUMMARY — source: {self._community_label(docs)}]\n"
                f"{self.graphrag.community_summaries[cid]}"
            )
            community_blocks.append(block)
            block_docs[block] = docs
        facts = self.graphrag.describe_entities(ranked["entities"])
        fact_blocks = [f"[KNOWLEDGE GRAPH]\n{facts}"] if facts else []
        logger.debug(f"_graph_extras: global_question={is_global}, community_limit={limit}")
        return community_blocks, fact_blocks, block_docs

    def _passage_block(self, p: Dict) -> str:
        # The source lets the LLM keep passages from different documents apart.
        label = "PASSAGE" if p["level"] == 0 else f"SUMMARY — level {p['level']}"
        return f"[{label} — source: {self._source_label(p.get('doc_ids', []))}]\n{p['text']}"

    async def _build_context(
        self, question: str, query_emb: List[float], method: Method, top_k: int,
        context_budget: Optional[int] = None, graph_extras: bool = True,
    ) -> Tuple[str, List[str], List[Dict]]:
        """The context string, its source documents, and the passages used (id, level,
        doc_ids, score) in rank order, for retrieval metrics such as Recall@k."""
        pool = max(top_k, _BUDGET_POOL) if context_budget else top_k
        community_blocks: List[str] = []
        fact_blocks: List[str] = []
        block_docs: Dict[str, List[str]] = {}

        if method == "standard":
            passages = self.raptor.retrieve(query_emb, top_k=pool, level=0)
        else:
            ranked = await self.hippo.retrieve(question, query_emb, top_k=pool)
            passages = ranked["passages"]
            if graph_extras:
                community_blocks, fact_blocks, block_docs = await self._graph_extras(question, query_emb, ranked)

        if context_budget:
            # Extras get at most _MAX_EXTRAS_SHARE of the budget (best communities first, graph
            # facts last), so they can't crowd out the passages that hold the facts.
            extras_cap = int(context_budget * _MAX_EXTRAS_SHARE)
            kept, used = [], 0
            for b in community_blocks + fact_blocks:
                if used + len(b) + 2 > extras_cap:
                    continue   # skip a block that doesn't fit; a later, smaller one still may
                kept.append(b)
                used += len(b) + 2
            community_blocks = [b for b in community_blocks if b in kept]
            fact_blocks = [b for b in fact_blocks if b in kept]
        extras = community_blocks + fact_blocks
        if context_budget:
            # Extras count against the budget; passages fill the rest, best first. The top
            # passage is always kept, even when it alone is over budget.
            used = sum(len(b) + 2 for b in extras)
            chosen: List[Dict] = []
            for p in passages:
                cost = len(self._passage_block(p)) + 2
                if chosen and used + cost > context_budget:
                    break
                chosen.append(p)
                used += cost
            passages = chosen
        else:
            passages = passages[:top_k]

        blocks = [self._passage_block(p) for p in passages] + community_blocks + fact_blocks
        sources: List[str] = []
        for p in passages:
            sources.extend(p.get("doc_ids", []))
        for b in community_blocks:
            sources.extend(block_docs.get(b, []))

        context = "\n\n".join(blocks)
        used_passages = [
            {"id": p["id"], "level": p["level"], "doc_ids": p.get("doc_ids", []), "score": p.get("score")}
            for p in passages
        ]
        logger.debug(
            f"_build_context: method={method!r}, passages={len(passages)}, extras={len(extras)}, "
            f"context_len={len(context)}, budget={context_budget}"
        )
        return context, list(dict.fromkeys(sources)), used_passages

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
        self, question: str, method: Method = "refined", top_k: int = 6,
        context_budget: Optional[int] = None, graph_extras: bool = True,
        temperature: float = 0.3,
    ) -> Dict:
        """context_budget: characters of context for both methods (None: top_k passages).
        graph_extras: refined adds community summaries and graph facts (False for ablations).
        temperature: the answer's sampling temperature (the evaluation uses 0)."""
        logger.info(f"QueryEngine.query: method={method!r}, top_k={top_k}, question={question[:80]!r}")
        query_emb = await self._embed_query(question)
        context, sources, used_passages = await self._build_context(
            question, query_emb, method, top_k, context_budget, graph_extras,
        )

        prompt = _ANSWER_PROMPT.format(
            context=context,
            history=self._history_str(),
            question=question,
        )

        try:
            raw = await generate(prompt, json_mode=True, temperature=temperature)
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
            # Not part of the HTTP response (QueryResponse ignores them); the evaluation
            # uses them to measure retrieval separately from generation.
            "context": context,
            "passages": used_passages,
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