import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class SubAreaOption(BaseModel):
    """廣域模糊地名之具體子商圈/候選點建議"""

    name: str = Field(description="子商圈、市鎮或特色商圈名稱（例如「熱海溫泉街」）")
    desc: str = Field(default="", description="特色亮點簡述（例如「海景、溫泉布丁」）")
    search_loc: Optional[str] = Field(
        default=None,
        description="送往 Google Places 檢索之具體地名或地標（若為 None 則使用 name）",
    )


class AreaDisambiguationOutput(BaseModel):
    """廣域或模糊地名快速消歧義判定結構化輸出合約"""

    is_location_ambiguous: bool = Field(
        default=False,
        description="地點是否為廣域都道府縣、大型海島/地區、或缺乏單一 800m 檢索中心點之模糊區域（例如：靜岡溫泉街、北海道、長野滑雪、九州）",
    )
    ambiguity_reason: Optional[str] = Field(
        default=None,
        description="若過於廣域模糊，簡要說明原因",
    )
    suggested_sub_areas: list[SubAreaOption] = Field(
        default_factory=list,
        description="若地點過於廣域模糊，提供 2~3 個最具代表性、且有明確 800m 車站或商圈中心的候選點（含 name, desc, search_loc）",
    )


class IntentOutput(BaseModel):
    """Stage 1 意圖解析結構化輸出合約 (借鏡 PydanticAI Schema-Driven & Semantic Validation)"""

    intent: Literal[
        "search",
        "more",
        "follow_up",
        "chitchat",
        "need_location",
        "explore_cuisine",
        "non_food",
        "out_of_region",
    ] = Field(
        description='意圖分類：搜尋特定餐廳 ("search")、探索當地特色美食與文化 ("explore_cuisine")、換一批/風格轉向 ("more")、追問上一輪店家 ("follow_up")、閒聊 ("chitchat")、缺地點 ("need_location")、非餐飲購物服務 ("non_food")、非日本境外地點 ("out_of_region")'
    )
    non_food_item: Optional[str] = Field(
        default=None,
        description="當 intent 為 non_food 時，使用者提及的非餐飲物品或服務名稱（例如「眼鏡」、「球鞋」、「藥妝」、「理髮」）",
    )
    explore_category: Optional[Literal["food", "drink", "souvenir"]] = Field(
        default=None,
        description='當 intent 為 explore_cuisine 時的探索子類別："food"(特色美食料理)、"drink"(在地飲品/名茶/地酒/喫茶)、"souvenir"(伴手禮/名菓禮盒)。若使用者未特別指定則設為 "food"',
    )
    location: Optional[str] = Field(
        default=None,
        description="日本地名、車站、商圈或景點名稱（若未提及請設為 null）",
    )
    cuisine: Optional[str] = Field(
        default=None,
        description='料理類別或食物核心名詞（例如「壽喜燒」、「拉麵」，嚴禁包含距離或時間詞；若 search 未指定則預設為 "熱門人氣美食"）',
    )
    modifier: Optional[str] = Field(
        default=None,
        description='修飾語或新要求（例如 "更在地老店"、"平價"、"走路15分鐘內"，若無請設為 null）',
    )
    target_index: Optional[int] = Field(
        default=None,
        ge=1,
        le=3,
        description="當 intent 為 follow_up 時，追問的單一店家序號（整數 1, 2 或 3，若未指定特定序號則為 null）",
    )
    target_indices: list[int] = Field(
        default_factory=list,
        description="當 intent 為 follow_up 且涉及多店對比或追問時的店家序號清單（如 [1, 2] 表示比較第 1 家與第 2 家）",
    )
    question: Optional[str] = Field(
        default=None,
        description="當 intent 為 follow_up 時，使用者追問的具體問題內容",
    )
    time_constraint: Optional[Literal["late_night", "breakfast", "now"]] = Field(
        default=None,
        description='使用者時間或營業狀態約束："late_night"(宵夜/深夜，22:00後)、"breakfast"(早餐/早午餐，10:30前)、"now"(現在/營業中)。若未提及請設為 null',
    )
    dietary_restrictions: list[str] = Field(
        default_factory=list,
        description='硬性飲食禁忌清單（如 ["不吃牛", "不吃辣"]）',
    )
    payment_preferences: list[str] = Field(
        default_factory=list,
        description='支付方式偏好清單（如 ["credit_card", "paypay"]）',
    )
    is_location_ambiguous: bool = Field(
        default=False,
        description="地點是否過於廣域、模糊或缺乏單一 800m 檢索中心點（例如都道府縣、整座海島、或者跨多市鎮之概念名詞如「靜岡溫泉街」、「長野滑雪」、「北海道」）",
    )
    ambiguity_reason: Optional[str] = Field(
        default=None,
        description="判定地點模糊之原因簡述",
    )
    suggested_sub_areas: list[SubAreaOption] = Field(
        default_factory=list,
        description="若地點過於廣域模糊，請提供 2~3 個最具代表性的具體子商圈或城鎮名（含 name、desc、search_loc）",
    )

    @model_validator(mode="after")
    def validate_semantic_consistency(self) -> "IntentOutput":
        """跨欄位語意一致性驗證器 (借鏡 PydanticAI @result_validator)"""
        # 1. 正規化空白字串為 None
        for attr in ("location", "cuisine", "modifier", "question"):
            val = getattr(self, attr)
            if isinstance(val, str):
                cleaned = val.strip()
                setattr(self, attr, cleaned if cleaned else None)

        # 2. 若為 search 且未指定 cuisine，自動補齊預設值
        if self.intent == "search" and not self.cuisine:
            self.cuisine = "熱門人氣美食"

        # 3. 雙向維護 target_index 與 target_indices
        if self.target_index is not None and not self.target_indices:
            self.target_indices = [self.target_index]
        elif self.target_indices and self.target_index is None:
            self.target_index = self.target_indices[0]

        # 4. 若為 follow_up，必須至少具備 target_index/target_indices 或 question，否則觸發 Reflection 自癒重試
        if self.intent == "follow_up" and self.target_index is None and not self.target_indices and not self.question:
            raise ValueError(
                "When intent is 'follow_up', either 'target_indices' (e.g. [1, 2]), 'target_index' (1-3) or 'question' must be provided."
            )

        return self

    @classmethod
    def build_json_schema_prompt(cls) -> str:
        """從 Pydantic Model 單一真相來源自動生成結構化 Prompt 規範字串"""
        schema = cls.model_json_schema()
        props: dict[str, Any] = schema.get("properties", {})
        field_lines: list[str] = []
        for field_name, info in props.items():
            desc = info.get("description", "")
            field_lines.append(f'  "{field_name}": {desc}')
        fields_block = ",\n".join(field_lines)
        return f"{{\n{fields_block}\n}}"


class FSMRunContext(BaseModel):
    """4 階段 FSM 執行期強型別上下文容器 (借鏡 PydanticAI RunContext[Deps])"""

    chat_id: Optional[int] = None
    user_message: str
    user_location: Optional[tuple[float, float]] = None
    intent: str = "search"
    explore_category: Optional[str] = None
    location_name: Optional[str] = None
    coords: Optional[tuple[float, float]] = None
    cuisine: str = "熱門美食"
    modifier: Optional[str] = None
    search_keyword: str = "熱門美食"
    time_constraint: Optional[str] = None
    radii_ladder: list[int] = Field(default_factory=lambda: [800, 1200, 1800])
    style_tag: Optional[str] = None
    exclude_place_ids: set[str] = Field(default_factory=set)
    long_term_preferences: dict[str, list[str]] = Field(default_factory=dict)


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
