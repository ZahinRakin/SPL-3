"""RAPTOR tree shape: no summary of a single node, one root per document, full summary input."""
import asyncio
from typing import Dict, List

import pytest

from backend.pipeline.raptor_runner import RaptorRunner


def _chunks(n: int, doc_id: str = "doc") -> List[Dict]:
    # The marker sits at the end of a long chunk: the old summariser cut each child to 600 chars.
    return [
        {"id": f"{doc_id}_c{i}", "text": ("filler " * 200) + f"MARKER{i}", "doc_id": doc_id}
        for i in range(n)
    ]


def _build(n: int) -> RaptorRunner:
    raptor = RaptorRunner()
    asyncio.run(raptor.build_tree(_chunks(n)))
    return raptor


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6, 8, 12, 20, 40])
def test_no_single_child_summaries(models, n):
    raptor = _build(n)
    summaries = [node for node in raptor.nodes.values() if node.level > 0]
    assert all(len(s.children) >= 2 for s in summaries)


@pytest.mark.parametrize("n", [1, 2])
def test_tiny_documents_get_no_summaries(models, n):
    raptor = _build(n)
    assert all(node.level == 0 for node in raptor.nodes.values())


@pytest.mark.parametrize("n", [3, 5, 6, 12, 40])
def test_tree_stops_at_one_root(models, n):
    raptor = _build(n)
    roots = [node for node in raptor.nodes.values() if node.parent is None]
    assert len(roots) == 1
    assert roots[0].level > 0


def test_every_chunk_is_under_the_root(models):
    raptor = _build(12)
    root = next(node for node in raptor.nodes.values() if node.parent is None)

    def leaves(node_id: str) -> List[str]:
        node = raptor.nodes[node_id]
        return [node_id] if node.level == 0 else [l for c in node.children for l in leaves(c)]

    assert len(leaves(root.id)) == 12


def test_summariser_sees_full_child_text(models):
    _build(4)
    assert len(models.prompts) == 1
    for i in range(4):
        assert f"MARKER{i}" in models.prompts[0]


def test_passages_are_one_documents_nodes(models):
    raptor = RaptorRunner()
    asyncio.run(raptor.build_tree(_chunks(4, "a")))
    asyncio.run(raptor.build_tree(_chunks(3, "b")))
    a = raptor.passages("a")
    assert {p["doc_id"] for p in a} == {"a"}
    assert sorted(p["level"] for p in a) == [0, 0, 0, 0, 1]
