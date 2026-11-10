import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class IntentOutput(BaseModel):
    """Stage 1 意圖解析結構化輸出合約"""

    intent: Literal["search", "more", "follow_up", "chitchat", "need_location"]
    location: Optional[str] = None
    cuisine: Optional[str] = None
    modifier: Optional[str] = None
    target_index: Optional[int] = Field(default=None, ge=1, le=3)
    question: Optional[str] = None
    dietary_restrictions: list[str] = Field(default_factory=list)
    payment_preferences: list[str] = Field(default_factory=list)


class GeocodeArgs(BaseModel):
    """geocode_location 工具輸入參數驗證"""

    query: str = Field(..., min_length=1)

    @field_validator("query")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Query string cannot be blank")
        return cleaned


class SearchNearbyArgs(BaseModel):
    """search_nearby 工具輸入參數驗證（含日本國境經緯度與搜尋半徑邊界）"""

    lat: float = Field(..., ge=20.0, le=46.0)
    lng: float = Field(..., ge=122.0, le=154.0)
    radius: int = Field(default=800, ge=100, le=5000)
    keyword: str = ""


class TabelogInfoArgs(BaseModel):
    """get_tabelog_info 工具輸入參數驗證"""

    google_place_id: str = Field(..., min_length=1)
    store_name: str = ""
    phone: str = ""
    lat: float = 0.0
    lng: float = 0.0

    @field_validator("google_place_id")
    @classmethod
    def validate_place_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("google_place_id cannot be blank")
        return cleaned


_DIETARY_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("不吃牛", re.compile(r"(?:不吃牛|忌牛|不能吃牛|無牛)", re.IGNORECASE)),
    ("不吃豬", re.compile(r"(?:不吃豬|忌豬|不能吃豬|清真|halal)", re.IGNORECASE)),
    ("不吃生食", re.compile(r"(?:不吃生食|不吃生魚片|不吃生|忌生食|全熟)", re.IGNORECASE)),
    ("不吃辣", re.compile(r"(?:不吃辣|不能吃辣|怕辣|完全不辣)", re.IGNORECASE)),
]

_PAYMENT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("credit_card", re.compile(r"(?:刷卡|信用卡|credit\s*card|visa|mastercard|jcb)", re.IGNORECASE)),
    ("paypay", re.compile(r"(?:paypay|電子支付|行動支付)", re.IGNORECASE)),
]


def extract_preferences_from_text(text: str) -> dict[str, list[str]]:
    """從使用者輸入文字中以確定性正則抽取飲食禁忌與支付偏好"""
    if not text:
        return {"dietary_restrictions": [], "payment_preferences": []}

    dietary: list[str] = []
    for label, pattern in _DIETARY_RULES:
        if pattern.search(text) and label not in dietary:
            dietary.append(label)

    payment: list[str] = []
    for label, pattern in _PAYMENT_RULES:
        if pattern.search(text) and label not in payment:
            payment.append(label)

    return {
        "dietary_restrictions": dietary,
        "payment_preferences": payment,
    }
