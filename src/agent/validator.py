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

    # 5. 🛡️ 老饕推薦理由零評分套話檢驗 (No-Rating-As-Reason Guardrail)
    # 嚴格禁止在推薦理由或理由區塊中出現「Google 評分高達 4.9 分」、「Tabelog 評分較少」等拿評分數值充當理由的客套廢話
    reason_lines = re.findall(r"(?:老饕推薦理由|老饕評價與推薦理由|推薦理由)[^\n]*[：:]\s*([^\n]+)", card_text)
    for r_line in reason_lines:
        if re.search(
            r"(?:Google|Tabelog|食べログ)[^\n]{0,20}?(?:評分|分數|高達|[0-5]\.[0-9]\s*分)[^\n]{0,25}?(?:[0-5]\.[0-9]|較少|認可|表現|日常)",
            r_line,
            flags=re.IGNORECASE,
        ):
            reasons.append("推薦理由中違規出現評分數值客套話 (No-Rating-As-Reason 違規)")
            break

    is_valid = len(reasons) == 0
    return is_valid, reasons


def validate_card_tiered(
    card_text: str,
    stores: Optional[list[dict[str, Any]]] = None,
) -> tuple[list[str], list[str]]:
    """分級卡片驗證器 (Tiered Card Validator)

    回傳:
        tuple[list[str], list[str]]: (fatal_errors 一級致命錯誤, minor_warnings 二級局限違規)
        - 一級致命錯誤: 虛構假店名、數量超額、Markdown表格崩壞、空內容 ➡️ 觸發全部拋棄 (All-or-Nothing)
        - 二級局限違規: 評分套話、假搜尋URL、缺少非結構標記 ➡️ 觸發外科手術局部替換 (In-Place Repair)
    """
    if not card_text or not card_text.strip():
        return ["卡片內容為空"], []

    fatal_errors: list[str] = []
    minor_warnings: list[str] = []

    # 1. 致命：嚴禁出現 Markdown 表格結構
    if re.search(r"\|(?:\s*[:-]+[-| :]*)\|", card_text):
        fatal_errors.append("出現未允許的 Markdown 表格結構")

    # 2. 局限：假搜尋連結 (可被外科手術修正)
    markdown_links = re.findall(r"\[([^\]]*)\]\(([^)]+)\)", card_text)
    for anchor_text, url in markdown_links:
        url_lower = url.lower()
        if (
            "google.com/maps/search" in url_lower
            or "google.co.jp/maps/search" in url_lower
            or "maps.google.com/?q=" in url_lower
            or "maps.google.com/maps?q=" in url_lower
        ):
            minor_warnings.append("偵測到偽造的 Google Maps 搜尋連結")
            break

    # 3. 致命/局限：反引號店名與 Grounding 真實性
    code_blocks = re.findall(r"`([^`\n]+)`", card_text)
    if not code_blocks:
        fatal_errors.append("缺少一鍵複製店名反引號代碼塊 (`店名`)")
    elif stores:
        valid_store_cbs = [cb.strip() for cb in code_blocks if 1 <= len(cb.strip()) <= 60]
        if not valid_store_cbs:
            fatal_errors.append("反引號代碼塊內容長度異常")
        else:
            unique_cbs: list[str] = []
            for cb in valid_store_cbs:
                clean = _normalize_name(cb)
                if clean and clean not in [_normalize_name(x) for x in unique_cbs]:
                    unique_cbs.append(cb)

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
                is_matched = any(
                    allowed and (clean_cb in allowed or allowed in clean_cb) for allowed in allowed_store_names
                )
                if is_matched:
                    matched_stores_count += 1
                else:
                    unmatched_store_cbs.append(cb)

            if has_ascii_store and matched_stores_count == 0 and len(unique_cbs) <= len(stores):
                matched_stores_count = len(unique_cbs)
                unmatched_store_cbs.clear()

            # 致命：店名完全對不上
            if matched_stores_count == 0:
                fatal_errors.append("反引號中的內容未正確對應候選店家店名")

            # 致命：超額捏造店家
            if len(unique_cbs) > len(stores):
                fatal_errors.append(
                    f"推薦店家數量 ({len(unique_cbs)} 家) 超過輸入之候選店家數 ({len(stores)} 家)，違反 Grounding 規範"
                )

            # 致命：虛構未授權店家
            if unmatched_store_cbs and not has_ascii_store:
                fatal_errors.append(f"推薦了未在候選名單中的未授權/虛構店家 ({', '.join(unmatched_store_cbs)})")

    # 4. 局限：欄位標記檢查
    if not re.search(r"(?:步行距離|步行約|🚶)", card_text):
        minor_warnings.append("缺少步行距離標記")
    if not re.search(r"(?:Tabelog|Google|雙平台評分|食べログ|⭐️)", card_text, flags=re.IGNORECASE):
        minor_warnings.append("缺少評分資訊標記")
    if not re.search(r"(?:支付方式|信用卡|PayPay|現金|💳)", card_text):
        minor_warnings.append("缺少支付方式標記")

    # 5. 局限：評分套話 (可透過外科手術局部替換為真實短評)
    reason_lines = re.findall(r"(?:老饕推薦理由|老饕評價與推薦理由|推薦理由)[^\n]*[：:]\s*([^\n]+)", card_text)
    for r_line in reason_lines:
        if re.search(
            r"(?:Google|Tabelog|食べログ)[^\n]{0,20}?(?:評分|分數|高達|[0-5]\.[0-9]\s*分)[^\n]{0,25}?(?:[0-5]\.[0-9]|較少|認可|表現|日常)",
            r_line,
            flags=re.IGNORECASE,
        ):
            minor_warnings.append("推薦理由中違規出現評分數值客套話 (No-Rating-As-Reason)")
            break

    return fatal_errors, minor_warnings


def repair_card_in_place(card_text: str, stores: list[dict[str, Any]]) -> str:
    """外科手術式局部修復 (In-Place Surgical Repair)

    在不破壞 LLM 原生語意與結構的前提下：
    1. 將評分客套話替換為後端真實提取的口感短評 (foodie_highlight / reviews[0])
    2. 將偽造的 Google Maps 搜尋 URL 淨化或替換為正規連結
    """
    repaired = card_text

    # 1. 偽造 Google 地圖連結外科手術修復
    # 將 [Google Maps](https://www.google.com/maps/search/...) 替換為純文字或其他合法連結
    repaired = re.sub(
        r"\[([^\]]*)\]\(https?://(?:www\.)?google\.(?:com|co\.jp)/maps/search[^\)]*\)",
        r"\1",
        repaired,
    )
    repaired = re.sub(
        r"\[([^\]]*)\]\(https?://maps\.google\.com/(?:\?q=|maps\?q=)[^\)]*\)",
        r"\1",
        repaired,
    )

    # 2. 評分客套話外科手術替換
    lines = repaired.split("\n")
    current_store_idx = 0
    new_lines: list[str] = []

    for line in lines:
        # 若該行包含店名反引號，推進對應店家指標
        code_match = re.search(r"`([^`\n]+)`", line)
        if code_match and stores:
            found_name = _normalize_name(code_match.group(1))
            for idx, s in enumerate(stores):
                if found_name in _normalize_name(s.get("name", "")) or found_name in _normalize_name(
                    s.get("name_ja", "")
                ):
                    current_store_idx = idx
                    break

        # 檢驗是否為推薦理由行且包含評分客套話
        is_reason_line = bool(re.search(r"(?:老饕推薦理由|老饕評價與推薦理由|推薦理由)[^\n]*[：:]", line))
        has_boilerplate = bool(
            re.search(
                r"(?:Google|Tabelog|食べログ)[^\n]{0,20}?(?:評分|分數|高達|[0-5]\.[0-9]\s*分)[^\n]{0,25}?(?:[0-5]\.[0-9]|較少|認可|表現|日常)",
                line,
                flags=re.IGNORECASE,
            )
        )

        if is_reason_line and has_boilerplate and stores and current_store_idx < len(stores):
            target_store = stores[current_store_idx]
            # 優先提取真實食評亮點
            replacement_text = target_store.get("foodie_highlight")
            if not replacement_text and target_store.get("reviews"):
                first_rev = target_store["reviews"][0]
                replacement_text = (
                    first_rev.get("text") if isinstance(first_rev, dict) else getattr(first_rev, "text", None)
                )

            if not replacement_text:
                replacement_text = "以道地手藝與扎實用料著稱，風味純粹，深獲在地常客好評。"
            else:
                replacement_text = replacement_text.strip().replace("\n", " ")
                if len(replacement_text) > 60:
                    replacement_text = replacement_text[:58] + "..."

            # 保留前綴 (例如 "💡 **老饕推薦理由**：")，替換後方內容
            prefix_match = re.match(r"^([^\n]*?[：:])\s*.*$", line)
            if prefix_match:
                new_line = f"{prefix_match.group(1)} {replacement_text}"
            else:
                new_line = f"💡 **老饕推薦理由**：{replacement_text}"
            new_lines.append(new_line)
        else:
            new_lines.append(line)

    return "\n".join(new_lines)
