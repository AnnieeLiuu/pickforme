from typing import Any, Optional

from pydantic import BaseModel, Field

from src.models.review import ReviewItem, ReviewSource


class StoreContext(BaseModel):
    """強型別店家全域上下文模型 (Typed Store Context Invariant)

    貫穿 State 1~4 全鏈路，確保 Google Places 原始資料、Tabelog 對齊結果、
    多源支付方式與強型別食評血統永不遺失。
    """

    name: str = Field(..., description="店家名稱")
    place_id: Optional[str] = Field(None, description="Google Places ID")
    address: Optional[str] = Field(None, description="店家完整地址")
    prefecture: Optional[str] = Field(None, description="解析後之都道府縣代碼 (如 tokyo, kanagawa)")
    lat: Optional[float] = Field(None, description="緯度")
    lng: Optional[float] = Field(None, description="經度")
    phone: Optional[str] = Field(None, description="聯絡電話")

    # 評分指標
    google_rating: Optional[float] = Field(None, description="Google Places 評分 (1.0~5.0)")
    google_user_ratings_total: Optional[int] = Field(None, description="Google 評價總則數")
    tabelog_rating: Optional[float] = Field(None, description="食べログ 評分 (如 3.58)")
    tabelog_review_count: Optional[int] = Field(None, description="食べログ 評價總則數")
    tabelog_url: Optional[str] = Field(None, description="食べログ 官方專頁 URL")

    # 榮譽與特色
    hyakumeiten: list[str] = Field(default_factory=list, description="百名店獲獎紀錄")
    awards: list[str] = Field(default_factory=list, description="獲獎紀錄 (The Tabelog Award 等)")
    price_lunch: Optional[str] = Field(None, description="午餐預算區間")
    price_dinner: Optional[str] = Field(None, description="晚餐預算區間")

    # 支付與設施
    payment_options: dict[str, Any] = Field(default_factory=dict, description="支付方式聚合資料")

    # 食評血統
    reviews: list[ReviewItem] = Field(default_factory=list, description="強型別多源食評清單")
    foodie_review_source: ReviewSource = Field(ReviewSource.UNKNOWN, description="卡片主推食評血統 (tabelog/google)")
    foodie_highlight: Optional[str] = Field(None, description="RAG 向量重排命中之老饕亮點短評")
    distance_meters: Optional[int] = Field(None, description="距離搜尋錨點之公尺數")

    def has_authentic_tabelog_reviews(self) -> bool:
        """檢查是否已成功覆蓋具備 Tabelog 老饕短評"""
        return any(r.source == ReviewSource.TABELOG and r.is_authentic_foodie for r in self.reviews)

    def to_legacy_dict(self) -> dict[str, Any]:
        """向後相容現有 Stage 4 模板與 Prompt 格式之字典轉換"""
        return {
            "name": self.name,
            "place_id": self.place_id,
            "address": self.address,
            "lat": self.lat,
            "lng": self.lng,
            "phone": self.phone,
            "rating": self.google_rating,
            "user_ratings_total": self.google_user_ratings_total,
            "tabelog_rating": self.tabelog_rating,
            "tabelog_review_count": self.tabelog_review_count,
            "tabelog_url": self.tabelog_url,
            "hyakumeiten": self.hyakumeiten,
            "awards": self.awards,
            "price_lunch": self.price_lunch,
            "price_dinner": self.price_dinner,
            "payment_options": self.payment_options,
            "reviews": [r.to_dict() for r in self.reviews],
            "foodie_review_source": self.foodie_review_source.value,
            "foodie_highlight": self.foodie_highlight,
            "distance_meters": self.distance_meters,
        }
