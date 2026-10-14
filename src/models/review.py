from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ReviewSource(str, Enum):
    """食評與顧客評價數據來源血統標籤 (Data Lineage Source)"""

    TABELOG = "tabelog"
    GOOGLE = "google"
    EDITORIAL = "editorial"
    UNKNOWN = "unknown"


class ReviewItem(BaseModel):
    """強型別多源食評模型 (Typed Review Lineage Model)"""

    text: str = Field(..., description="評論內文 (口感、氛圍或用餐體驗)")
    source: ReviewSource = Field(..., description="數據源 (Tabelog / Google / Editorial)")
    author: Optional[str] = Field(None, description="作者或暱稱")
    rating: Optional[float] = Field(None, description="該則評論給出之星數 (若有)")
    original_text: Optional[str] = Field(None, description="原始語言未翻譯文本")
    is_authentic_foodie: bool = Field(True, description="是否為通過 ReviewSanitizer 之真人口感食評")

    def to_dict(self) -> dict[str, Any]:
        """相容既有字典格式之平滑轉換方法"""
        return {
            "text": self.text,
            "source": self.source.value,
            "author": self.author,
            "rating": self.rating,
            "original_text": self.original_text or self.text,
            "is_authentic_foodie": self.is_authentic_foodie,
        }
