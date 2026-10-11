"""卡片排版驗證器 (Card Layout Validator)

確保 Agent 產生的美食卡片遵循 Telegram Markdown 排版規範：
1. 包含一鍵複製店名 (反引號 `店名`)
2. 包含步行距離 (🚶 **步行距離**)
3. 包含雙平台評分 (⭐️ **雙平台評分**)
4. 包含支付方式 (💳 **支付方式**)
5. 嚴禁出現 Markdown 表格排版 (|---|)
"""

import re
from typing import Any, Optional


def validate_recommendation_card(
    card_text: str,
    stores: Optional[list[dict[str, Any]]] = None,
) -> tuple[bool, list[str]]:
    """驗證美食推薦卡片格式是否符合規範。

    回傳:
        tuple[bool, list[str]]: (是否合格, 未通過原因清單)
    """
    if not card_text or not card_text.strip():
        return False, ["卡片內容為空"]

    reasons: list[str] = []

    # 1. 嚴禁出現 Markdown 表格 (包含表頭分隔線 |---| 或 |:---:|)
    if re.search(r"\|(?:\s*[:-]+[-| :]*)\|", card_text):
        reasons.append("出現未允許的 Markdown 表格結構")

    # 2. 檢驗是否包含反引號 Code block (一鍵複製店名)
    code_blocks = re.findall(r"`([^`\n]+)`", card_text)
    if not code_blocks:
        reasons.append("缺少一鍵複製店名反引號代碼塊 (`店名`)")
    elif stores:
        # 若有傳入候選店家，確認至少有一家店名被反引號包覆
        matched_any = False
        for s in stores:
            name_ja = s.get("name_ja") or s.get("name") or ""
            if any(name_ja in cb or cb in name_ja for cb in code_blocks if cb.strip()):
                matched_any = True
                break
        if not matched_any and len(code_blocks) < len(stores):
            reasons.append("反引號中的內容未正確對應候選店家店名")

    # 3. 檢驗關鍵欄位標記
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
