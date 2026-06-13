from typing import Dict, List, Literal
from pydantic import BaseModel

class QueryRequest(BaseModel):
    question: str
    method: Literal["graphrag", "raptor", "hippo", "hybrid"] = "hybrid"
    top_k: int = 6

class QueryResponse(BaseModel):
    answer: str
    entities: List[Dict]
    sources: List[str]
    confidence: float
    reasoning: str
    method: str
