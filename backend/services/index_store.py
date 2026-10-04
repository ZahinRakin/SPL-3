"""
Save and load one case's pipeline state to and from Postgres.

The pipeline exposes plain dicts (export_state / load_state); this module maps them to rows.
Saving is a "replace snapshot": delete the case's derived rows, bulk-insert the current ones.
Louvain re-runs over the whole case graph after every document, so communities and entity
rows change globally anyway; replacing is simple and always consistent (ARCHITECTURE.md §8).
"""
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.logger import logger
from backend.models import (
    CaseIndexMeta, ChatMessage, Chunk, CommunityRow, EntityMention, EntityRow, HippoNodeRow,
    RaptorNodeRow, RelationshipRow,
)
from backend.pipeline.graphrag_indexer import GraphRAGIndexer
from backend.pipeline.hippo_retriever import HippoRetriever
from backend.pipeline.raptor_runner import RaptorRunner

HISTORY_TURNS = 3   # QueryEngine only uses the last 3 turns in its prompt


def _uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    return uuid.UUID(value) if value else None


def _floats(vec: Optional[List[float]]) -> Optional[List[float]]:
    # NumPy scalars (from mean-pooling) must become plain floats before binding.
    return [float(x) for x in vec] if vec is not None else None


async def _bulk_insert(session: AsyncSession, model: Any, rows: List[Dict]) -> None:
    if rows:
        await session.execute(insert(model), rows)


# ── save ──────────────────────────────────────────────────────────────────────

async def save_chunks(session: AsyncSession, case_id: uuid.UUID, document_id: uuid.UUID, chunks: List[Dict]) -> None:
    await _bulk_insert(session, Chunk, [
        {"id": c["id"], "document_id": document_id, "case_id": case_id, "chunk_index": i, "text": c["text"]}
        for i, c in enumerate(chunks)
    ])


async def save_case_state(
    session: AsyncSession,
    case_id: uuid.UUID,
    graphrag: GraphRAGIndexer,
    raptor: RaptorRunner,
    hippo: HippoRetriever,
) -> None:
    """Replace the case's derived index rows. Runs inside the caller's transaction."""
    g = graphrag.export_state()
    r = raptor.export_state()
    h = hippo.export_state()

    # entity_mentions and relationships go with entities via ON DELETE CASCADE.
    for model in (EntityRow, CommunityRow, RaptorNodeRow, HippoNodeRow):
        await session.execute(delete(model).where(model.case_id == case_id))

    node_community = g["node_community"]
    entity_rows, mention_rows = [], []
    for seq, e in enumerate(g["entities"]):
        eid = uuid.UUID(e["id"])
        entity_rows.append({
            "id": eid, "case_id": case_id, "seq": seq,
            "name": e["name"] or "", "normalized_key": (e["name"] or "").lower(),
            "type": e["type"], "description": e["description"] or "",
            "community": node_community.get(e["id"], -1),
        })
        for pos, chunk_id in enumerate(dict.fromkeys(e["source_chunks"])):
            mention_rows.append({"entity_id": eid, "chunk_id": chunk_id, "position": pos})

    rel_rows = [
        {
            "id": uuid.UUID(rel["id"]), "case_id": case_id, "seq": seq,
            "source_entity_id": uuid.UUID(rel["source_id"]),
            "target_entity_id": uuid.UUID(rel["target_id"]),
            "relation_type": rel["relation_type"] or "RELATED_TO",
            "description": rel["description"] or "",
            "weight": float(rel["weight"]),
        }
        for seq, rel in enumerate(g["relationships"])
    ]
    community_rows = [
        {"case_id": case_id, "community_id": int(cid), "summary": summary or ""}
        for cid, summary in g["communities"].items()
    ]
    raptor_rows = [
        {
            "id": uuid.UUID(n["id"]), "case_id": case_id, "seq": seq, "level": n["level"],
            "text": n["text"] or "", "parent_id": _uuid(n["parent"]),
            "children": [uuid.UUID(c) for c in n["children"]],
            "doc_ids": [str(d) for d in n["doc_ids"]],
            "embedding": _floats(n["embedding"]),
        }
        for seq, n in enumerate(r["nodes"])
    ]
    hippo_rows = [
        {
            "id": uuid.UUID(n["id"]), "case_id": case_id, "seq": seq, "level": n["level"],
            "text": n["text"] or "", "parent_id": _uuid(n["parent_id"]),
            "child_ids": [uuid.UUID(c) for c in n["child_ids"]],
            "doc_id": str(n["doc_id"] or ""), "chunk_idx": n["chunk_idx"],
            "embedding": _floats(n["embedding"]),
        }
        for seq, n in enumerate(h["nodes"])
    ]

    await _bulk_insert(session, EntityRow, entity_rows)
    await _bulk_insert(session, EntityMention, mention_rows)
    await _bulk_insert(session, RelationshipRow, rel_rows)
    await _bulk_insert(session, CommunityRow, community_rows)
    await _bulk_insert(session, RaptorNodeRow, raptor_rows)
    await _bulk_insert(session, HippoNodeRow, hippo_rows)

    meta = {"case_id": case_id, "raptor_root_ids": r["root_ids"], "hippo_levels": h["levels"]}
    await session.execute(
        pg_insert(CaseIndexMeta).values(**meta).on_conflict_do_update(
            index_elements=[CaseIndexMeta.case_id],
            set_={"raptor_root_ids": meta["raptor_root_ids"], "hippo_levels": meta["hippo_levels"]},
        )
    )
    logger.debug(
        f"save_case_state: case={case_id}, entities={len(entity_rows)}, relationships={len(rel_rows)}, "
        f"raptor={len(raptor_rows)}, hippo={len(hippo_rows)}"
    )


# ── load ──────────────────────────────────────────────────────────────────────

async def load_case_state(session: AsyncSession, case_id: uuid.UUID) -> Dict:
    """Everything load_state() needs for the case's GraphRAG, RAPTOR, HiPPO and QueryEngine."""
    entities = (await session.execute(
        select(EntityRow).where(EntityRow.case_id == case_id).order_by(EntityRow.seq)
    )).scalars().all()
    mentions = (await session.execute(
        select(EntityMention.entity_id, EntityMention.chunk_id)
        .join(EntityRow, EntityRow.id == EntityMention.entity_id)
        .where(EntityRow.case_id == case_id)
        .order_by(EntityMention.entity_id, EntityMention.position)
    )).all()
    chunks_by_entity: Dict[uuid.UUID, List[str]] = {}
    for entity_id, chunk_id in mentions:
        chunks_by_entity.setdefault(entity_id, []).append(chunk_id)

    rels = (await session.execute(
        select(RelationshipRow).where(RelationshipRow.case_id == case_id).order_by(RelationshipRow.seq)
    )).scalars().all()
    communities = (await session.execute(
        select(CommunityRow).where(CommunityRow.case_id == case_id)
    )).scalars().all()
    raptor_nodes = (await session.execute(
        select(RaptorNodeRow).where(RaptorNodeRow.case_id == case_id).order_by(RaptorNodeRow.seq)
    )).scalars().all()
    hippo_nodes = (await session.execute(
        select(HippoNodeRow).where(HippoNodeRow.case_id == case_id).order_by(HippoNodeRow.seq)
    )).scalars().all()
    meta = await session.get(CaseIndexMeta, case_id)
    recent = (await session.execute(
        select(ChatMessage.question, ChatMessage.answer)
        .where(ChatMessage.case_id == case_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(HISTORY_TURNS)
    )).all()

    def _vec(v: Any) -> Optional[List[float]]:
        return [float(x) for x in v] if v is not None else None

    state = {
        "graphrag": {
            "entities": [
                {
                    "id": str(e.id), "name": e.name, "type": e.type, "description": e.description,
                    "source_chunks": chunks_by_entity.get(e.id, []),
                }
                for e in entities
            ],
            "relationships": [
                {
                    "id": str(r.id), "source_id": str(r.source_entity_id), "target_id": str(r.target_entity_id),
                    "relation_type": r.relation_type, "description": r.description, "weight": r.weight,
                }
                for r in rels
            ],
            "communities": {c.community_id: c.summary for c in communities},
            "node_community": {str(e.id): e.community for e in entities},
        },
        "raptor": {
            "nodes": [
                {
                    "id": str(n.id), "text": n.text, "level": n.level,
                    "children": [str(c) for c in n.children],
                    "parent": str(n.parent_id) if n.parent_id else None,
                    "embedding": _vec(n.embedding), "doc_ids": list(n.doc_ids),
                }
                for n in raptor_nodes
            ],
            "root_ids": list(meta.raptor_root_ids) if meta else [],
        },
        "hippo": {
            "nodes": [
                {
                    "id": str(n.id), "text": n.text, "embedding": _vec(n.embedding), "level": n.level,
                    "parent_id": str(n.parent_id) if n.parent_id else None,
                    "child_ids": [str(c) for c in n.child_ids],
                    "doc_id": n.doc_id, "chunk_idx": n.chunk_idx,
                }
                for n in hippo_nodes
            ],
            "levels": dict(meta.hippo_levels) if meta else {},
        },
        "engine": {
            "history": [{"question": q, "answer": a} for q, a in reversed(recent)],
        },
    }
    logger.debug(
        f"load_case_state: case={case_id}, entities={len(entities)}, raptor={len(raptor_nodes)}, "
        f"hippo={len(hippo_nodes)}, history={len(recent)}"
    )
    return state
