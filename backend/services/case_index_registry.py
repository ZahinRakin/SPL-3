"""
One pipeline bundle (RAPTOR + GraphRAG + HippoRAG + QueryEngine) per case (decision D11).

Bundles are loaded lazily from Postgres and kept in a small LRU cache, because the
dev machine has ~8 GB of RAM. Each bundle has its own lock so two uploads to the same
case don't mutate the in-memory indexes at the same time.
"""
import asyncio
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from backend.core.config import settings
from backend.core.database import session_scope
from backend.core.logger import logger
from backend.pipeline.graphrag_indexer import GraphRAGIndexer
from backend.pipeline.hippo_retriever import HippoRetriever
from backend.pipeline.query_engine import QueryEngine
from backend.pipeline.raptor_runner import RaptorRunner
from backend.services.index_store import load_case_state


@dataclass
class CaseBundle:
    graphrag: GraphRAGIndexer
    raptor: RaptorRunner
    hippo: HippoRetriever
    engine: QueryEngine
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)   # serialises indexing within a case


class CaseIndexRegistry:
    def __init__(self, max_cases: int = settings.INDEX_CACHE_MAX_CASES):
        self.max_cases = max_cases
        self._cache: "OrderedDict[uuid.UUID, CaseBundle]" = OrderedDict()
        self._load_lock = asyncio.Lock()

    async def get(self, case_id: uuid.UUID) -> CaseBundle:
        bundle = self._cache.get(case_id)
        if bundle is not None:
            self._cache.move_to_end(case_id)
            return bundle
        async with self._load_lock:
            bundle = self._cache.get(case_id)    # another request may have loaded it meanwhile
            if bundle is None:
                bundle = await self._load(case_id)
                self._cache[case_id] = bundle
                self._evict_overflow()
            return bundle

    def evict(self, case_id: uuid.UUID) -> None:
        if self._cache.pop(case_id, None) is not None:
            logger.debug(f"CaseIndexRegistry: evicted case {case_id}")

    async def reload(self, case_id: uuid.UUID, bundle: CaseBundle) -> None:
        """Reset a bundle to what Postgres holds (e.g. after a failed indexing run left it
        half-updated). Done in place so tasks already waiting on bundle.lock see clean state."""
        await self._fill(case_id, bundle)

    # ── internals ─────────────────────────────────────────────────────────────

    async def _load(self, case_id: uuid.UUID) -> CaseBundle:
        graphrag, raptor = GraphRAGIndexer(), RaptorRunner()
        # HippoRAG keeps no state of its own; it ranks over this case's graph and RAPTOR nodes.
        hippo = HippoRetriever(graphrag=graphrag, raptor=raptor)
        bundle = CaseBundle(graphrag, raptor, hippo, QueryEngine(graphrag=graphrag, raptor=raptor, hippo=hippo))
        await self._fill(case_id, bundle)
        logger.info(f"CaseIndexRegistry: loaded case {case_id} ({len(graphrag.entities)} entities)")
        return bundle

    async def _fill(self, case_id: uuid.UUID, bundle: CaseBundle) -> None:
        async with session_scope() as session:
            state = await load_case_state(session, case_id)
        # Rebuilding the graph can be slow for big cases; keep it off the event loop.
        await asyncio.to_thread(bundle.graphrag.load_state, state["graphrag"])
        bundle.raptor.load_state(state["raptor"])
        bundle.engine.load_state(state["engine"])

    def _evict_overflow(self) -> None:
        # Oldest first; never drop a bundle that is being indexed right now.
        for cid in list(self._cache.keys()):
            if len(self._cache) <= self.max_cases:
                break
            if not self._cache[cid].lock.locked():
                self.evict(cid)


case_index_registry = CaseIndexRegistry()
