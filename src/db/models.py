from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class RestaurantBase(BaseModel):
    google_place_id: str
    tabelog_url: Optional[str] = None
    name_ja: str
    name_en: Optional[str] = None
    phone: Optional[str] = None
    lat: float
    lng: float
    address: Optional[str] = None


class RestaurantCreate(RestaurantBase):
    pass


class Restaurant(RestaurantBase):
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=datetime.now)


class TabelogCacheBase(BaseModel):
    tabelog_rating: Optional[float] = None
    review_count: Optional[int] = None
    price_lunch: Optional[str] = None
    price_dinner: Optional[str] = None
    match_confidence: Optional[float] = None
    match_method: Optional[str] = None


class TabelogCache(TabelogCacheBase):
    restaurant_id: UUID
    updated_at: datetime = Field(default_factory=datetime.now)


class ChatSession(BaseModel):
    chat_id: int
    last_location_lat: Optional[float] = None
    last_location_lng: Optional[float] = None
    last_recommended_ids: Optional[list[str]] = None
    conversation_history: Optional[list[dict[str, Any]]] = None
    updated_at: datetime = Field(default_factory=datetime.now)


class PrewarmJob(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    area_name: str
    status: str = "pending"
    target_count: int = 20
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class AgentRun(BaseModel):
    run_id: UUID = Field(default_factory=uuid4)
    chat_id: Optional[int] = None
    user_query: str
    model_used: str
    total_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    status: str = "started"
    created_at: datetime = Field(default_factory=datetime.now)


class AgentStep(BaseModel):
    step_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    step_number: int
    action_type: str  # 'llm_call' | 'tool_call'
    tool_name: Optional[str] = None
    tool_input: Optional[dict[str, Any]] = None
    tool_output: Optional[dict[str, Any]] = None
    latency_ms: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
