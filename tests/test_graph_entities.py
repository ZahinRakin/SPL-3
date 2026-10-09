"""GraphRAG entity identity and relationship-endpoint resolution."""
import asyncio
import json
from typing import Dict, List, Tuple

from backend.pipeline.graphrag_indexer import GraphRAGIndexer, entity_key


def _index(extractions: Dict[str, Dict]) -> GraphRAGIndexer:
    """Index one document whose passages return the given extractions (no LLM)."""
    graph = GraphRAGIndexer()

    async def fake_extract(chunk: Dict) -> Tuple[List[Dict], List[Dict]]:
        data = extractions[chunk["id"]]
        return data.get("entities", []), data.get("relationships", [])

    async def no_communities() -> None:
        return None

    graph._extract = fake_extract
    graph._detect_communities = no_communities
    passages = [{"id": pid, "text": "", "doc_id": "doc"} for pid in extractions]
    asyncio.run(graph.index_document("doc", "", passages))
    return graph


def _ent(name: str, etype: str, desc: str = "") -> Dict:
    return {"name": name, "type": etype, "description": desc}


def _rel(source: str, target: str, relation: str = "RELATED_TO") -> Dict:
    return {"source": source, "target": target, "relation": relation}


def _edges(graph: GraphRAGIndexer) -> set:
    return {
        (graph.entities[s].name, d["relation"], graph.entities[t].name)
        for s, t, d in graph.graph.edges(data=True)
    } | {
        (graph.entities[t].name, d["relation"], graph.entities[s].name)
        for s, t, d in graph.graph.edges(data=True)
    }


def test_same_name_different_type_stays_two_entities():
    graph = _index({"p1": {"entities": [_ent("Ashford", "PERSON"), _ent("Ashford", "LOCATION")]}})
    assert sorted(e.type for e in graph.entities.values()) == ["LOCATION", "PERSON"]


def test_same_name_and_type_merges_across_passages():
    graph = _index({
        "p1": {"entities": [_ent("Ticagrelor", "DRUG", "antiplatelet")]},
        "p2": {"entities": [_ent("ticagrelor", "DRUG")]},
    })
    (ent,) = graph.entities.values()
    assert ent.source_chunks == ["p1", "p2"]


def test_typographic_punctuation_is_folded():
    graph = _index({"p1": {"entities": [_ent("BCR‑ABL1", "CONCEPT"), _ent("BCR-ABL1", "CONCEPT")]}})
    assert len(graph.entities) == 1


def test_short_endpoint_resolves_to_full_name():
    graph = _index({"p1": {
        "entities": [_ent("Quantum Financial Services, Inc.", "ORGANIZATION"), _ent("John Ashford", "PERSON")],
        "relationships": [_rel("Quantum", "John Ashford", "EMPLOYS")],
    }})
    assert ("Quantum Financial Services, Inc.", "EMPLOYS", "John Ashford") in _edges(graph)


def test_misspelled_endpoint_resolves():
    graph = _index({"p1": {
        "entities": [_ent("John Ashford", "PERSON"), _ent("Riverside Clinic", "ORGANIZATION")],
        "relationships": [_rel("John Ashfrod", "Riverside Clinic", "VISITED")],
    }})
    assert ("John Ashford", "VISITED", "Riverside Clinic") in _edges(graph)


def test_unlisted_endpoint_becomes_an_entity():
    graph = _index({"p1": {
        "entities": [_ent("John Ashford", "PERSON")],
        "relationships": [_rel("John Ashford", "SEC", "INVESTIGATED_BY")],
    }})
    assert any(e.name == "Sec" and e.type == "OTHER" for e in graph.entities.values())
    assert len(graph.relationships) == 1


def test_self_loops_and_empty_endpoints_are_dropped():
    graph = _index({"p1": {
        "entities": [_ent("John Ashford", "PERSON")],
        "relationships": [_rel("John Ashford", "John Ashford"), _rel("", "John Ashford")],
    }})
    assert graph.relationships == []


def test_alias_edges_link_variants_of_one_type_only():
    graph = _index({
        "p1": {"entities": [_ent("Quantum Financial Services", "ORGANIZATION"), _ent("Ashford", "LOCATION")]},
        "p2": {"entities": [_ent("Quantum Financial Services, Inc.", "ORGANIZATION"), _ent("John Ashford", "PERSON")]},
    })
    edges = _edges(graph)
    assert ("Quantum Financial Services", "SAME_AS", "Quantum Financial Services, Inc.") in edges
    assert not any(rel == "SAME_AS" and "Ashford" in a for a, rel, _ in edges)


def test_short_words_are_not_aliases():
    graph = _index({"p1": {"entities": [_ent("No", "CONCEPT"), _ent("No Tumor Lysis Syndrome", "CONCEPT")]}})
    assert graph.relationships == []


def test_state_round_trip_and_unique_keys():
    graph = _index({"p1": {
        "entities": [_ent("Ashford", "PERSON"), _ent("Ashford", "LOCATION"), _ent("Quantum", "ORGANIZATION")],
        "relationships": [_rel("Ashford", "Quantum")],
    }})
    state = json.loads(json.dumps(graph.export_state()))
    loaded = GraphRAGIndexer()
    loaded.load_state(state)
    assert loaded._name_to_id == graph._name_to_id
    assert sorted(loaded.graph.edges) == sorted(graph.graph.edges)
    # services/index_store.py stores this key under a unique constraint.
    keys = [entity_key(e["name"], e["type"]) for e in state["entities"]]
    assert len(keys) == len(set(keys))
