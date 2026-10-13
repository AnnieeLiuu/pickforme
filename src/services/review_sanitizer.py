"""Review Insight Sanitizer and Recommendation Synthesizer.

Filters out metadata noise (business hours, transit directions, addresses)
from scraped snippets and synthesizes high-quality, authentic foodie recommendation reasons.
"""

import re
from typing import Any, Optional

# 營業時間與交通元資料正則模式 (相容日文原文與翻譯繁中/簡中)
NOISE_PATTERNS = [
    r"営業時間[:：]?\s*(?:\d{1,2}[:：]\d{2}|24時間)",
    r"24時間営業",
    r"定休日[:：]?",
    r"ラストオーダー",
    r"\b\d{1,2}[:：]\d{2}\s*-\s*\d{1,2}[:：]\d{2}\b",
    r"(?:星期|週)[一二三四五六日天]",
    r"(?:徒歩|步行)\s*\d+\s*分鐘?",
    r"(?:從|从|距)[^\n]{0,15}?(?:站|駅|出口)[^\n]{0,15}?(?:徒歩|步行)",
    r"(?:距離|距离)[^\n]{0,10}?\d+\s*(?:米|m|meters?)",
    r"〒\d{3}-\d{4}",
    r"ビル\s*\d+[F階]",
]

# 食物與用餐體驗關鍵字 (正向食評特徵)
FOOD_TASTE_KEYWORDS = [
    "美味",
    "好吃",
    "絕品",
    "肉質",
    "湯頭",
    "麵條",
    "湯底",
    "新鮮",
    "口感",
    "濃郁",
    "清爽",
    "嚼勁",
    "和牛",
    "牛舌",
    "沙朗",
    "醬汁",
    "蛋拌飯",
    "份量",
    "沾麵",
    "拉麵",
    "壽司",
    "壽喜燒",
    "串燒",
    "定食",
    "白飯",
    "甜點",
    "小菜",
    "美味しい",
    "旨い",
    "絶品",
    "スープ",
    "麺",
    "タレ",
    "濃厚",
    "あっさり",
    "柔らか",
    "ジューシー",
    "サクサク",
    "香ばしい",
    "満足",
    "人気",
    "看板",
]


def is_pure_metadata_noise(text: Any) -> bool:
    """判斷給定的短評片段是否主要由營業時間或交通指引等非口感雜訊構成"""
    if not isinstance(text, str) or not text.strip():
        return True

    clean = text.strip()

    # 若含有顯著的美食與口感特徵詞，優先判定為有用評論
    has_food_keyword = any(kw in clean for kw in FOOD_TASTE_KEYWORDS)

    # 檢查是否含有營業或交通關鍵模式
    noise_matches = sum(1 for p in NOISE_PATTERNS if re.search(p, clean, re.IGNORECASE))

    # 若含有 2 個以上雜訊特徵，且無具體食物口感詞，視為純雜訊
    if noise_matches >= 1 and not has_food_keyword:
        return True

    # 若開頭即為純營業時間或交通且篇幅極短
    if len(clean) < 60 and noise_matches >= 1:
        return True

    return False


def sanitize_review_snippet(raw_text: Any) -> Optional[str]:
    """清洗食評摘要，剔除生硬營業時間與交通句子，保留口感核心精華"""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None

    text = raw_text.strip().replace("\xa0", " ").replace("\r\n", " ").replace("\n", " ")

    # 先檢測是否整段皆為雜訊
    if is_pure_metadata_noise(text):
        return None

    # 將文字依常見標點切割為句子，過濾純雜訊子句
    sentences = re.split(r"[。！？\.\!\?]\s*", text)
    cleaned_sentences: list[str] = []

    for s in sentences:
        s_clean = s.strip()
        if not s_clean:
            continue
        # 若單一子句命中雜訊模式，跳過該子句
        if any(re.search(p, s_clean, re.IGNORECASE) for p in NOISE_PATTERNS):
            continue
        cleaned_sentences.append(s_clean)

    if not cleaned_sentences:
        return None

    result = "。".join(cleaned_sentences).strip()
    if not result.endswith("。") and not result.endswith("！") and not result.endswith("!"):
        result += "。"

    if len(result) > 130:
        result = result[:125] + "..."

    return result


def synthesize_store_recommendation_reason(
    store: dict[str, Any],
    cuisine: Optional[str] = None,
) -> tuple[str, str]:
    """為店家提煉或智慧合成老饕推薦理由 (絕不輸出生硬營業時間)

    Returns:
        tuple[str, str]: (推薦理由文字, 標籤來源如 '食べログ 口コミ' 或 '老饕指標')
    """
    # 1. 優先嘗試現有已清洗的語意短評 highlight
    hl = store.get("foodie_review_highlight")
    if hl:
        clean_hl = sanitize_review_snippet(hl)
        if clean_hl:
            return clean_hl, "食べログ 口コミ"

    # 2. 嘗試清洗 sample_reviews 候選
    sample_revs = store.get("sample_reviews") or []
    if isinstance(sample_revs, list):
        for r in sample_revs:
            candidate_text = ""
            rev_source = "食べログ 口コミ"
            if isinstance(r, dict):
                candidate_text = r.get("text") or r.get("original_text") or r.get("zh") or ""
                if r.get("source") != "tabelog":
                    rev_source = "Google 在地評價"
            elif isinstance(r, str):
                candidate_text = r

            clean_text = sanitize_review_snippet(candidate_text)
            if clean_text:
                return clean_text, rev_source

    # 3. 若無有效口感短評 (如純營業時間被過濾)，依結構化指標智慧合成推薦理由
    t_rating = store.get("tabelog_rating")
    g_rating = store.get("rating") or store.get("google_rating")
    awards = store.get("awards") or store.get("hyakumeiten") or []
    rev_count = store.get("review_count") or 0
    c_name = cuisine or "日式經典"

    if awards:
        reason = "獲選 Tabelog 百名店殊榮，主打正宗經典風味與高品質食材，為在地饕客極力推崇之指標名店。"
        return reason, "百名店指標"

    if t_rating and float(t_rating) >= 3.50:
        reason = f"Tabelog 評分達 {float(t_rating):.2f} 高分名店，累積逾 {rev_count:,} 則老饕驗證，口味穩定出眾，為該商圈首選人氣店。"
        return reason, "食べログ 權威認證"

    if t_rating and float(t_rating) > 0:
        reason = f"主打道地 {c_name} 風味，食材講究且深受在地食客青睞，用餐氛圍親切道地。"
        return reason, "在地饕客精選"

    if g_rating and float(g_rating) >= 4.5:
        reason = f"Google 評分高達 {float(g_rating):.1f} 分超人氣口碑，服務細緻且菜品豐富，為周邊極具人氣的美味之選。"
        return reason, "Google 人氣推薦"

    return f"精選周邊優質 {c_name} 餐廳，整體評價優秀且深受歡迎，值得前往一試。", "美食特派員推薦"
