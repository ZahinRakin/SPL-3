from datetime import datetime
from typing import Dict, List, Literal, Optional
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

class ChatMessageOut(BaseModel):
    """One stored question/answer turn of a case's chat history."""
    id: str
    question: str
    method: str
    top_k: int
    answer: str
    reasoning: str
    confidence: float
    entities: List[Dict]
    sources: List[str]
    latency_ms: int
    user_id: Optional[str]
    user_name: Optional[str]
    created_at: datetime
