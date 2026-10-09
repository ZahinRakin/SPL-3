"""
Save and load one case's pipeline state to and from Postgres.

The pipeline exposes plain dicts (export_state / load_state); this module maps them to rows:
RAPTOR nodes → passages, GraphRAG entities/relations/communities → their tables, and each
entity's source passages → entity_mentions. HippoRAG has no state of its own.
Saving is a "replace snapshot": delete the case's derived rows, bulk-insert the current ones.
Louvain re-runs over the whole case graph after every document, so communities and entity
rows change globally anyway; replacing is simple and always consistent (ARCHITECTURE.md §8).
"""
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.logger import logger
from backend.models import (
    ChatMessage, CommunityRow, EntityMention, EntityRow, PassageRow, RelationshipRow,
)
from backend.pipeline.graphrag_indexer import GraphRAGIndexer
from backend.pipeline.raptor_runner import RaptorRunner

HISTORY_TURNS = 3   # QueryEngine only uses the last 3 turns in its prompt


def _uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    return uuid.UUID(value) if value else None


def _floats(vec: Optional[List[float]]) -> Optional[List[float]]:
    # NumPy scalars must become plain floats before binding.
    return [float(x) for x in vec] if vec is not None else None


async def _bulk_insert(session: AsyncSession, model: Any, rows: List[Dict]) -> None:
    if rows:
        await session.execute(insert(model), rows)


# ── save ──────────────────────────────────────────────────────────────────────

async def save_case_state(
    session: AsyncSession,
    case_id: uuid.UUID,
    graphrag: GraphRAGIndexer,
    raptor: RaptorRunner,
) -> None:
    """Replace the case's derived index rows. Runs inside the caller's transaction."""
    g = graphrag.export_state()
    r = raptor.export_state()

    # entity_mentions and relationships go with entities/passages via ON DELETE CASCADE.
    for model in (EntityRow, CommunityRow, PassageRow):
        await session.execute(delete(model).where(model.case_id == case_id))

    passage_rows = [
        {
            "id": uuid.UUID(n["id"]), "case_id": case_id, "seq": seq, "level": n["level"],
            "text": n["text"] or "", "parent_id": _uuid(n["parent"]),
            "children": [uuid.UUID(c) for c in n["children"]],
            "doc_ids": [str(d) for d in n["doc_ids"]],
            "embedding": _floats(n["embedding"]),
        }
        for seq, n in enumerate(r["nodes"])
    ]
    passage_ids = {row["id"] for row in passage_rows}

    node_community = g["node_community"]
    entity_rows, mention_rows = [], []
    unlinked = 0
    for seq, e in enumerate(g["entities"]):
        eid = uuid.UUID(e["id"])
        entity_rows.append({
            "id": eid, "case_id": case_id, "seq": seq,
            # Same identity as graphrag_indexer.entity_key: one entity per (name, type).
            "name": e["name"] or "", "normalized_key": f"{(e['name'] or '').lower()}|{e['type']}",
            "type": e["type"], "description": e["description"] or "",
            "community": node_community.get(e["id"], -1),
        })
        for pos, pid in enumerate(dict.fromkeys(e["source_chunks"])):
            # Only passages can be linked. A plain chunk id appears only when RAPTOR failed
            # and the graph was built from raw chunks (D6); that mention can't be stored.
            try:
                passage_id = uuid.UUID(pid)
            except ValueError:
                passage_id = None
            if passage_id in passage_ids:
                mention_rows.append({"entity_id": eid, "passage_id": passage_id, "position": pos})
            else:
                unlinked += 1

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

    # Passages before mentions (FK).
    await _bulk_insert(session, PassageRow, passage_rows)
    await _bulk_insert(session, EntityRow, entity_rows)
    await _bulk_insert(session, EntityMention, mention_rows)
    await _bulk_insert(session, RelationshipRow, rel_rows)
    await _bulk_insert(session, CommunityRow, community_rows)

    if unlinked:
        logger.warning(f"save_case_state: case={case_id}, {unlinked} entity mentions have no passage, not stored")
    logger.debug(
        f"save_case_state: case={case_id}, passages={len(passage_rows)}, entities={len(entity_rows)}, "
        f"mentions={len(mention_rows)}, relationships={len(rel_rows)}"
    )


# ── load ──────────────────────────────────────────────────────────────────────

async def load_case_state(session: AsyncSession, case_id: uuid.UUID) -> Dict:
    """Everything load_state() needs for the case's GraphRAG, RAPTOR and QueryEngine."""
    entities = (await session.execute(
        select(EntityRow).where(EntityRow.case_id == case_id).order_by(EntityRow.seq)
    )).scalars().all()
    mentions = (await session.execute(
        select(EntityMention.entity_id, EntityMention.passage_id)
        .join(EntityRow, EntityRow.id == EntityMention.entity_id)
        .where(EntityRow.case_id == case_id)
        .order_by(EntityMention.entity_id, EntityMention.position)
    )).all()
    passages_by_entity: Dict[uuid.UUID, List[str]] = {}
    for entity_id, passage_id in mentions:
        passages_by_entity.setdefault(entity_id, []).append(str(passage_id))

    rels = (await session.execute(
        select(RelationshipRow).where(RelationshipRow.case_id == case_id).order_by(RelationshipRow.seq)
    )).scalars().all()
    communities = (await session.execute(
        select(CommunityRow).where(CommunityRow.case_id == case_id)
    )).scalars().all()
    passages = (await session.execute(
        select(PassageRow).where(PassageRow.case_id == case_id).order_by(PassageRow.seq)
    )).scalars().all()
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
                    "source_chunks": passages_by_entity.get(e.id, []),
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
                for n in passages
            ],
        },
        "engine": {
            "history": [{"question": q, "answer": a} for q, a in reversed(recent)],
        },
    }
    logger.debug(
        f"load_case_state: case={case_id}, entities={len(entities)}, passages={len(passages)}, "
        f"history={len(recent)}"
    )
    return state
