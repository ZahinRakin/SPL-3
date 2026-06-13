from backend.pipeline.graphrag_indexer import GraphRAGIndexer
from backend.pipeline.raptor_runner import RaptorRunner
from backend.pipeline.hippo_retriever import HippoRetriever
from backend.pipeline.query_engine import QueryEngine
from backend.core.logger import logger

graphrag = GraphRAGIndexer()
raptor = RaptorRunner()
hippo = HippoRetriever()
query_engine = QueryEngine(graphrag=graphrag, raptor=raptor, hippo=hippo)
logger.info("Pipeline components initialized: GraphRAGIndexer, RaptorRunner, HippoRetriever, QueryEngine")

def get_graphrag() -> GraphRAGIndexer:
    return graphrag

def get_raptor() -> RaptorRunner:
    return raptor

def get_hippo() -> HippoRetriever:
    return hippo

def get_query_engine() -> QueryEngine:
    return query_engine
