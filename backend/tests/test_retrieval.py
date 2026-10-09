"""HippoRAG hybrid ranking and the refined/standard context (community summaries, source labels)."""
import asyncio
import math
from typing import Dict, List

import numpy as np
import pytest

from backend.pipeline.graphrag_indexer import Entity, GraphRAGIndexer
from backend.pipeline.hippo_retriever import HippoRetriever
from backend.pipeline.query_engine import QueryEngine
from backend.pipeline.raptor_runner import RaptorNode, RaptorRunner
from backend.pipeline.vectors import EMBED_DIM

from tests.conftest import fake_vector

QUERY = [1.0] + [0.0] * (EMBED_DIM - 1)


def _similar(cos: float, salt: str) -> List[float]:
    """A unit vector with cosine ≈ `cos` to QUERY."""
    noise = np.array(fake_vector(salt))
    noise[0] = 0.0
    noise /= np.linalg.norm(noise)
    return (cos * np.array(QUERY) + math.sqrt(1 - cos ** 2) * noise).tolist()


class Case:
    """Two documents: stemi (chunks s0-s2) and leukemia (l0-l2), plus three summaries."""

    def __init__(self) -> None:
        self.raptor = RaptorRunner()
        self.graph = GraphRAGIndexer()
        sims = {"s0": 0.60, "s1": 0.20, "s2": 0.10, "l0": 0.55, "l1": 0.15, "l2": 0.05}
        for pid, cos in sims.items():
            doc = "stemi" if pid.startswith("s") else "leukemia"
            self._node(pid, 0, cos, doc)
        # Summaries that are *more* similar than any chunk: only two may enter the top-k.
        for pid in ("sum_a", "sum_b", "sum_c"):
            self._node(pid, 1, 0.90, "stemi")
        self.entity("Aspirin", "DRUG", ["s2"], community=0)
        self.entity("Clopidogrel", "DRUG", ["s1"], community=0)
        self.entity("Imatinib", "DRUG", ["l1"], community=1)
        self.entity("Bone Marrow", "CONCEPT", ["l2"], community=1)
        self.link("Aspirin", "Clopidogrel")
        self.link("Imatinib", "Bone Marrow")
        for cid in range(8):
            self.graph.community_summaries[cid] = f"community {cid} summary"
        self.engine = QueryEngine(
            graphrag=self.graph, raptor=self.raptor,
            hippo=HippoRetriever(graphrag=self.graph, raptor=self.raptor),
        )

    def _node(self, pid: str, level: int, cos: float, doc: str) -> None:
        self.raptor.nodes[pid] = RaptorNode(id=pid, text=f"text of {pid}", level=level,
                                            embedding=_similar(cos, pid), doc_ids=[doc])

    def entity(self, name: str, etype: str, passages: List[str], community: int) -> str:
        ent = Entity(id=name, name=name, type=etype, source_chunks=passages)
        self.graph.entities[ent.id] = ent
        self.graph._name_to_id[f"{name.lower()}|{etype}"] = ent.id
        self.graph.graph.add_node(ent.id, name=name, type=etype, community=community)
        return ent.id

    def link(self, a: str, b: str) -> None:
        self.graph._add_edge(a, b, "RELATED_TO", "")

    def retrieve(self, question: str, top_k: int = 6) -> Dict:
        return asyncio.run(self.engine.hippo.retrieve(question, QUERY, top_k=top_k))

    def context(self, question: str, method: str = "refined") -> str:
        return asyncio.run(self.engine.query(question, method=method))["context"]


@pytest.fixture
def case(models) -> Case:
    return Case()


# ── HippoRAG ranking ──────────────────────────────────────────────────────────

def test_summaries_are_capped(case):
    passages = case.retrieve("anything at all", top_k=6)["passages"]
    assert sum(1 for p in passages if p["level"] > 0) == 2


def test_named_entity_lifts_its_chunk(case):
    # The graph weight is tuned on dev data (EVALUATION_PLAN §4); pin it so this tests the
    # mechanism (graph rank can lift a chunk) rather than the default value.
    case.engine.hippo.ppr_weight = 2.0
    ranked_ids = [p["id"] for p in case.retrieve("What dose of aspirin was given?", top_k=8)["passages"]]
    # s2 is the least similar stemi chunk, but it is the one that mentions aspirin.
    assert ranked_ids.index("s2") < ranked_ids.index("s1")
    assert ranked_ids.index("s2") < ranked_ids.index("l1")


def test_summary_inherits_its_chunks_graph_rank(case):
    # sum_a summarises the aspirin chunk: with the graph term it must be able to beat
    # chunks that have neither high similarity nor a graph rank.
    # sum_a, sum_b, sum_c are equally similar; only sum_a has a graph-ranked chunk under it.
    case.raptor.nodes["sum_a"].children = ["s2"]
    case.engine.hippo.ppr_weight = 1.0
    case.engine.hippo.max_summaries = 3
    ranked = [p["id"] for p in case.retrieve("What dose of aspirin was given?", top_k=9)["passages"]]
    assert ranked.index("sum_a") < ranked.index("sum_b")
    assert ranked.index("sum_a") < ranked.index("sum_c")
    assert ranked.index("sum_a") < ranked.index("l2")


def test_without_a_graph_ranking_is_similarity(case):
    case.graph.entities.clear()
    case.graph.graph.clear()
    chunks = [p["id"] for p in case.retrieve("anything", top_k=8)["passages"] if p["level"] == 0]
    assert chunks == ["s0", "l0", "s1", "l1", "s2", "l2"]


def test_community_mass_follows_the_seeds(case):
    mass = case.retrieve("What dose of aspirin was given?")["communities"]
    assert mass[0] > mass.get(1, 0.0)


# ── context assembly ──────────────────────────────────────────────────────────

def test_source_labels_use_document_names(case):
    case.engine.doc_names = {"stemi": "STEMI case.txt"}
    context = case.context("What dose of aspirin was given?", method="standard")
    assert "[PASSAGE — source: STEMI case.txt]" in context
    assert "[PASSAGE — source: leukemia]" in context   # no name known: the id is shown


@pytest.mark.parametrize("question, expected", [
    ("What dose of aspirin was given?", 2),
    ("What are the overall themes across all documents?", 6),
    ("Summarise the key findings", 6),
])
def test_refined_adds_community_summaries(case, question, expected):
    assert case.context(question).count("[COMMUNITY SUMMARY") == expected


def test_standard_mode_has_no_graph_context(case):
    context = case.context("What are the overall themes across all documents?", method="standard")
    assert "[COMMUNITY SUMMARY" not in context
    assert "[KNOWLEDGE GRAPH]" not in context


def test_community_summary_names_its_documents(case):
    context = case.context("What dose of aspirin was given?")
    assert "[COMMUNITY SUMMARY — source: stemi]\ncommunity 0 summary" in context


def test_community_label_is_capped_and_big_blocks_are_skipped(case):
    # A community whose entities come from many documents: the label names the 3 most-mentioned.
    docs = [f"doc{i}" for i in range(40)]
    label = case.engine._community_label(docs)
    assert label.startswith("doc0, doc1, doc2") and label.endswith("(+37 more)")
    # With a budget, an oversized community block is skipped, not a reason to drop the rest.
    case.graph.community_summaries[0] = "x" * 5000
    context = asyncio.run(case.engine._build_context(
        "What dose of aspirin was given?", QUERY, "refined", 6, context_budget=4000, graph_extras=True))[0]
    assert "x" * 5000 not in context
    assert "[KNOWLEDGE GRAPH]" in context


def test_broad_questions_cover_every_document():
    ranked = [1, 2, 3, 4, 5]                       # 1-4 come from the big document
    docs = {1: ["big"], 2: ["big"], 3: ["big"], 4: ["big"], 5: ["small"]}
    assert QueryEngine._select_communities(ranked, docs, 3, cover_documents=False) == [1, 2, 3]
    assert QueryEngine._select_communities(ranked, docs, 3, cover_documents=True) == [1, 2, 5]


def test_community_embeddings_are_cached_until_summaries_change(case):
    case.context("What dose of aspirin was given?")
    first = case.engine._community_index
    case.context("What dose of imatinib was given?")
    assert case.engine._community_index is first
    case.graph.community_summaries[0] = "rewritten after an upload"
    case.context("What dose of imatinib was given?")
    assert case.engine._community_index is not first
