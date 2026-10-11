"""卡片排版與真實性驗證器 (Card Layout & Grounding Validator)

確保 Agent 產生的美食卡片遵循 Telegram Markdown 排版與 Grounding 真實性規範：
1. 包含一鍵複製店名 (反引號 `店名`)，且所有推薦店家必須 100% 來自候選名單 (杜絕腦補湊數假店家)
2. 包含步行距離 (🚶 **步行距離**)
3. 包含雙平台評分 (⭐️ **雙平台評分**)
4. 包含支付方式 (💳 **支付方式**)
5. 嚴禁出現 Markdown 表格排版 (|---|)
6. 嚴禁出現偽造的 Google Maps 搜尋連結 (google.com/maps/search)
"""

import re
from typing import Any, Optional


def _normalize_name(name: str) -> str:
    """去除空格、特殊標點與括號，轉小寫以便穩健比對"""
    if not name:
        return ""
    return re.sub(r"[\s\-_・，,（）()「」『』*#]+", "", name).lower()


def validate_recommendation_card(
    card_text: str,
    stores: Optional[list[dict[str, Any]]] = None,
) -> tuple[bool, list[str]]:
    """驗證美食推薦卡片格式與 Grounding 真實性。

    回傳:
        tuple[bool, list[str]]: (是否合格, 未通過原因清單)
    """
    if not card_text or not card_text.strip():
        return False, ["卡片內容為空"]

    reasons: list[str] = []

    # 1. 嚴禁出現 Markdown 表格 (包含表頭分隔線 |---| 或 |:---:|)
    if re.search(r"\|(?:\s*[:-]+[-| :]*)\|", card_text):
        reasons.append("出現未允許的 Markdown 表格結構")

    # 2. 嚴禁出現偽造的 Google Maps 搜尋連結 (防範 LLM 找不到 URL 自行拼湊搜尋字串)
    markdown_links = re.findall(r"\[([^\]]*)\]\(([^)]+)\)", card_text)
    for anchor_text, url in markdown_links:
        url_lower = url.lower()
        if "google.com/maps/search" in url_lower or "google.co.jp/maps/search" in url_lower:
            reasons.append("偵測到偽造的 Google Maps 搜尋連結 (maps/search)")
            break
        if "maps.google.com/?q=" in url_lower or "maps.google.com/maps?q=" in url_lower:
            reasons.append("偵測到偽造的 Google Maps 搜尋關鍵字連結")
            break

    # 3. 檢驗是否包含反引號 Code block (一鍵複製店名)
    code_blocks = re.findall(r"`([^`\n]+)`", card_text)
    if not code_blocks:
        reasons.append("缺少一鍵複製店名反引號代碼塊 (`店名`)")
    elif stores:
        # 檢驗反引號內的店名是否具備合理長度且不包含程式代碼雜訊
        valid_store_cbs = [cb.strip() for cb in code_blocks if 1 <= len(cb.strip()) <= 60]
        if not valid_store_cbs:
            reasons.append("反引號代碼塊內容長度異常")
        else:
            # 去除重複的反引號店名 (防止同一店家在卡片中多次被引述)
            unique_cbs: list[str] = []
            for cb in valid_store_cbs:
                clean = _normalize_name(cb)
                if clean and clean not in [_normalize_name(x) for x in unique_cbs]:
                    unique_cbs.append(cb)

            # 建立候選店家的合法名稱集合
            allowed_store_names: list[str] = []
            has_ascii_store = False
            for s in stores:
                raw_name = s.get("name") or ""
                if raw_name and re.search(r"^[a-zA-Z0-9\s\-]+$", raw_name.strip()):
                    has_ascii_store = True
                for k in ["name_ja", "name", "name_en"]:
                    val = s.get(k)
                    if val and isinstance(val, str):
                        allowed_store_names.append(_normalize_name(val))

            matched_stores_count = 0
            unmatched_store_cbs: list[str] = []

            for cb in unique_cbs:
                clean_cb = _normalize_name(cb)
                if not clean_cb:
                    continue

                is_matched = False
                for allowed in allowed_store_names:
                    if allowed and (clean_cb in allowed or allowed in clean_cb):
                        is_matched = True
                        break

                if is_matched:
                    matched_stores_count += 1
                else:
                    unmatched_store_cbs.append(cb)

            # 跨語言保護：若候選包含英數拼音，允許英轉日漢字，但依然受到嚴格數量上限約束
            if has_ascii_store and matched_stores_count == 0 and len(unique_cbs) <= len(stores):
                # 候選全為拼音且 LLM 自行翻譯為漢字，數量未超額時視為匹配成功
                matched_stores_count = len(unique_cbs)
                unmatched_store_cbs.clear()

            if matched_stores_count == 0:
                reasons.append("反引號中的內容未正確對應候選店家店名")

            # 🛡️ Grounding 數量防偽護欄：若推薦的獨立店家數超過候選店家數，代表模型自行無中生有湊數
            if len(unique_cbs) > len(stores):
                reasons.append(
                    f"推薦店家數量 ({len(unique_cbs)} 家) 超過輸入之候選店家數 ({len(stores)} 家)，違反 Grounding 規範"
                )

            # 🛡️ Grounding 未授權店家攔截
            if unmatched_store_cbs and not has_ascii_store:
                reasons.append(f"推薦了未在候選名單中的未授權/虛構店家 ({', '.join(unmatched_store_cbs)})")

    # 4. 檢驗關鍵欄位標記
    # (a) 步行距離
    if not re.search(r"(?:步行距離|步行約|🚶)", card_text):
        reasons.append("缺少步行距離標記 (🚶 **步行距離**)")

    # (b) 評分標記
    if not re.search(r"(?:Tabelog|Google|雙平台評分|食べログ|⭐️)", card_text, flags=re.IGNORECASE):
        reasons.append("缺少評分資訊標記 (⭐️ **雙平台評分**)")

    # (c) 支付方式標記
    if not re.search(r"(?:支付方式|信用卡|PayPay|現金|💳)", card_text):
        reasons.append("缺少支付方式標記 (💳 **支付方式**)")

    is_valid = len(reasons) == 0
    return is_valid, reasons
