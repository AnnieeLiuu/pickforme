"""卡片排版與真實性驗證器 (Card Layout & Grounding Validator)

確保 Agent 產生的美食卡片遵循 Telegram Markdown 排版與 Grounding 真實性規範：
1. 包含一鍵複製店名 (反引號 `店名`)，且所有推薦店家必須 100% 來自候選名單 (杜絕腦補湊數假店家)
2. 包含步行距離 (🚶 **步行距離**)
3. 包含雙平台評分 (⭐️ **雙平台評分**)
4. 包含支付方式 (💳 **支付方式**)
5. 嚴禁出現 Markdown 表格排版 (|---|)
6. 嚴禁出現偽造的 Google Maps 搜尋連結 (google.com/maps/search)
"""

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    """去除空格、特殊標點與括號，轉小寫以便穩健比對"""
    if not name:
        return ""
    return re.sub(r"[\s\-_・，,（）()「」『』*#]+", "", name).lower()


def get_store_normalized_names(store: dict[str, Any]) -> list[str]:
    """取得店家所有可用於比對的正規化名稱 (name_ja / name / name_en)"""
    names: list[str] = []
    for k in ("name_ja", "name", "name_en"):
        val = store.get(k)
        if val and isinstance(val, str):
            norm = _normalize_name(val)
            if norm and norm not in names:
                names.append(norm)
    return names


def is_store_matched(candidate_text: str, store: dict[str, Any]) -> bool:
    """判斷候選文字是否與指定店家名稱相符 (正規化包含比對)"""
    clean_cand = _normalize_name(candidate_text)
    if not clean_cand:
        return False
    return any(
        allowed and (clean_cand in allowed or allowed in clean_cand) for allowed in get_store_normalized_names(store)
    )


def split_card_store_blocks(card_text: str) -> list[str]:
    """將推薦卡片依店家標題與結尾提醒切割為獨立區塊 (支援 1. / ### / 🥇🥈🥉 / 💡 特派員提醒)"""
    if not card_text:
        return []
    pattern = (
        r"(?m)(?=^(?:"
        r"(?:###\s*|(?:\*\*))?(?:[🥇🥈🥉]|\d+[\.、](?!\d))"
        r"|###\s*(?:`[^`\n]+`|\*\*[^*\n]+)"
        r"|💡\s*\*{0,2}特派員提醒"
        r"|---+\s*$"
        r"))"
    )
    return [part for part in re.split(pattern, card_text) if part]


_DETAIL_LINE_ICON_RE = re.compile(r"^\s*(?:[-•*]\s*)?(?:🚶|⭐️|⭐|💳|💰|🔗|📍|💡)")
_DETAIL_LINE_LABEL_RE = re.compile(
    r"^\s*(?:[-•*]\s*)?(?:\*\*)?(?:支付(?:方式)?|付款(?:方式)?|交通|步行(?:距離)?|預算|價位|營業(?:狀態|時間)?|地址|評分|雙平台評分|評價|老饕推薦理由|老饕評價與推薦理由|推薦理由|點評|特派員提醒|提醒|店家連結|連結)(?:\*\*)?\s*[：:]"
)
_STORE_LABEL_RE = re.compile(r"(?:🍱|店名|餐廳|店家)\s*(?:\*\*)?\s*[：:]")


def _is_detail_metadata_line(line: str) -> bool:
    """判斷是否為非店名之明細欄位行 (Fail-Closed：含店名標籤或無明確明細標籤者皆視為潛在店名行)"""
    if _STORE_LABEL_RE.search(line):
        return False
    return bool(_DETAIL_LINE_ICON_RE.search(line) or _DETAIL_LINE_LABEL_RE.search(line))


def _extract_store_code_blocks(card_text: str, stores: list[dict[str, Any]]) -> list[str]:
    """萃取應視為「店名主張」的反引號內容，排除明細欄中的非店名排版反引號 (如 `PayPay`)"""
    store_cbs: list[str] = []
    for line in card_text.splitlines():
        cbs = re.findall(r"`([^`\n]+)`", line)
        if not cbs:
            continue
        is_detail = _is_detail_metadata_line(line)
        for cb in cbs:
            cb_stripped = cb.strip()
            if not cb_stripped:
                continue
            if any(is_store_matched(cb_stripped, s) for s in stores):
                store_cbs.append(cb)
            elif not is_detail:
                store_cbs.append(cb)
    return store_cbs


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
    code_blocks = _extract_store_code_blocks(card_text, stores or [])
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

            matched_stores_count = 0
            unmatched_store_cbs: list[str] = []
            for cb in unique_cbs:
                if any(is_store_matched(cb, s) for s in stores):
                    matched_stores_count += 1
                else:
                    unmatched_store_cbs.append(cb)

            # 致命：店名完全對不上
            if matched_stores_count == 0:
                fatal_errors.append("反引號中的內容未正確對應候選店家店名")

            # 致命：超額捏造店家
            if len(unique_cbs) > len(stores):
                fatal_errors.append(
                    f"推薦店家數量 ({len(unique_cbs)} 家) 超過輸入之候選店家數 ({len(stores)} 家)，違反 Grounding 規範"
                )

            # 致命：虛構未授權店家 (嚴格禁止整批 Bypass)
            if unmatched_store_cbs:
                fatal_errors.append(f"推薦了未在候選名單中的未授權/虛構店家 ({', '.join(unmatched_store_cbs)})")

    # 4. 局限：欄位標記檢查
    if not re.search(r"(?:步行距離|步行約|🚶)", card_text):
        minor_warnings.append("缺少步行距離標記")
    if not re.search(r"(?:Tabelog|Google|雙平台評分|食べログ|⭐️)", card_text, flags=re.IGNORECASE):
        minor_warnings.append("缺少評分資訊標記")
    if not re.search(r"(?:支付方式|信用卡|PayPay|現金|💳)", card_text):
        minor_warnings.append("缺少支付方式標記")

    # 5. 局限：評分套話或生硬日文評論洩漏 (可透過外科手術局部替換為真實短評)
    reason_lines = re.findall(r"(?:老饕推薦理由|老饕評價與推薦理由|推薦理由)[^\n]*[：:]\s*([^\n]+)", card_text)
    for r_line in reason_lines:
        if re.search(
            r"(?:Google|Tabelog|食べログ)[^\n]{0,20}?(?:評分|分數|高達|[0-5]\.[0-9]\s*分)[^\n]{0,25}?(?:[0-5]\.[0-9]|較少|認可|表現|日常)",
            r_line,
            flags=re.IGNORECASE,
        ):
            minor_warnings.append("推薦理由中違規出現評分數值客套話 (No-Rating-As-Reason)")
            break
        if re.search(r"[\u3040-\u309F\u30A0-\u30FF]", r_line):
            minor_warnings.append("推薦理由中違規出現未翻譯之生硬日文原文 (Raw Japanese Leak)")
            break

    return fatal_errors, minor_warnings


_PROTECTED_SPAN_RE = re.compile(r"(`[^`\n]+`|\[[^\]]*\]\([^)]+\))")


def _wrap_name_outside_protected_spans(line: str, name_clean: str) -> tuple[str, bool]:
    """僅在既有的反引號與 Markdown 連結之外包裹店名，防止雙重反引號或破壞超連結"""
    parts = _PROTECTED_SPAN_RE.split(line)
    for idx in range(0, len(parts), 2):
        seg = parts[idx]
        if name_clean in seg:
            parts[idx] = seg.replace(name_clean, f"`{name_clean}`", 1)
            return "".join(parts), True
    return line, False


def repair_store_name_code_blocks(card_text: str, stores: list[dict[str, Any]]) -> str:
    """自動修復標題行中缺失的一鍵複製店名反引號代碼塊

    逐店檢查是否已被反引號包覆，僅對漏包店家進行修復，
    並保護既有反引號與 Markdown 連結不被重複包覆。
    """
    if not card_text or not stores:
        return card_text

    existing_cbs = re.findall(r"`([^`\n]+)`", card_text)
    lines = card_text.splitlines()

    for s in stores:
        if any(is_store_matched(cb, s) for cb in existing_cbs):
            continue

        candidates = [s.get("name_ja"), s.get("name"), s.get("name_en")]
        wrapped = False
        for name in candidates:
            if not name or not isinstance(name, str):
                continue
            name_clean = name.strip()
            if len(name_clean) < 2:
                continue

            # 第一輪：優先在非明細行 (標題/店名行) 尋找並包裹
            for idx, line in enumerate(lines):
                if _is_detail_metadata_line(line):
                    continue
                new_line, did_wrap = _wrap_name_outside_protected_spans(line, name_clean)
                if did_wrap:
                    lines[idx] = new_line
                    existing_cbs.append(name_clean)
                    wrapped = True
                    break
            if wrapped:
                break

    return "\n".join(lines)


def repair_card_in_place(card_text: str, stores: list[dict[str, Any]], cuisine: Optional[str] = None) -> str:
    """外科手術式局部修復 (In-Place Surgical Repair)

    在不破壞 LLM 原生語意與結構的前提下：
    1. 將評分客套話或日文生硬短評替換為結構化繁中口感短評 (絕無日文假名穿透)
    2. 將偽造的 Google Maps 搜尋 URL 淨化或替換為正規連結
    """
    from src.services.review_sanitizer import (
        contains_japanese_kana,
        synthesize_store_recommendation_reason,
    )

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

    # 2. 評分客套話與日文原文外科手術替換
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

        # 檢驗是否為店家連結行但未帶有合法 Markdown 超連結
        is_link_line = bool(re.search(r"🔗[^\n]*?(?:店家連結|連結)[^\n]*[：:]", line))
        has_markdown_url = bool(re.search(r"\[[^\]]+\]\(https?://[^\)]+\)", line))

        if is_link_line and not has_markdown_url and stores and current_store_idx < len(stores):
            target_store = stores[current_store_idx]
            t_url = target_store.get("tabelog_url")
            place_id = target_store.get("google_place_id") or target_store.get("id")
            g_url = target_store.get("google_maps_url") or (
                f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else None
            )
            if t_url:
                new_lines.append(f"🔗 **店家連結**：[Tabelog 頁面]({t_url})")
            elif g_url:
                new_lines.append(f"🔗 **店家連結**：[Google Maps 頁面]({g_url})")
            else:
                new_lines.append(line)
            continue

        # 檢驗是否為推薦理由行且包含評分客套話或日文假名
        is_reason_line = bool(re.search(r"(?:老饕推薦理由|老饕評價與推薦理由|推薦理由)[^\n]*[：:]", line))
        has_boilerplate = bool(
            re.search(
                r"(?:Google|Tabelog|食べログ)[^\n]{0,20}?(?:評分|分數|高達|[0-5]\.[0-9]\s*分)[^\n]{0,25}?(?:[0-5]\.[0-9]|較少|認可|表現|日常)",
                line,
                flags=re.IGNORECASE,
            )
        )
        has_raw_japanese = contains_japanese_kana(line)

        if is_reason_line and (has_boilerplate or has_raw_japanese) and stores and current_store_idx < len(stores):
            target_store = stores[current_store_idx]
            replacement_text, _ = synthesize_store_recommendation_reason(target_store, cuisine=cuisine)
            if not replacement_text or contains_japanese_kana(replacement_text):
                # 寧缺勿濫：無真實中文食評或獎項可替換時，直接省略該違規推薦理由行
                continue

            replacement_text = replacement_text.strip().replace("\n", " ")
            if len(replacement_text) > 90:
                replacement_text = replacement_text[:88] + "..."

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


def sanitize_single_store_ratings(text: str, store: dict[str, Any]) -> str:
    """校正單一店家區塊或單店追問回覆中的 Tabelog / Google 評分數值"""
    if not text or not store:
        return text

    updated = text
    t_rating = store.get("tabelog_rating")
    if t_rating is not None:
        t_val = float(t_rating)
        t_pattern = r"((?:Tabelog|食べログ)[^\d\n]{0,25}?)(\d\.\d{1,2})(\s*分)?"

        def _replace_tabelog(m: re.Match[str]) -> str:
            orig_val = float(m.group(2))
            unit = m.group(3) or ""
            if abs(orig_val - t_val) > 0.001:
                logger.info(
                    f"[Guardrail] Corrected Tabelog rating for '{store.get('name_ja') or store.get('name')}': "
                    f"{orig_val} -> {t_val:.2f}"
                )
                return f"{m.group(1)}{t_val:.2f}{unit}"
            return m.group(0)

        updated = re.sub(t_pattern, _replace_tabelog, updated, count=1)

    g_rating = store.get("google_rating") or store.get("rating")
    if g_rating is not None:
        g_val = float(g_rating)
        g_pattern = r"(Google[^\d\n]{0,20}?)([0-5]\.[0-9])(\s*分)?"

        def _replace_google(m: re.Match[str]) -> str:
            orig_val = float(m.group(2))
            unit = m.group(3) or ""
            if abs(orig_val - g_val) > 0.001:
                logger.info(
                    f"[Guardrail] Corrected Google rating for '{store.get('name_ja') or store.get('name')}': "
                    f"{orig_val} -> {g_val:.1f}"
                )
                return f"{m.group(1)}{g_val:.1f}{unit}"
            return m.group(0)

        updated = re.sub(g_pattern, _replace_google, updated, count=1)

    return updated


def validate_follow_up_response(
    response_text: str,
    stores: list[dict[str, Any]],
    extra_allowed_stores: Optional[list[dict[str, Any]]] = None,
) -> tuple[str, list[str]]:
    """追問回覆輸出驗證護欄 (Item 5)

    驗證追問與比較回覆：
    1. 非空且非異常過短
    2. 嚴禁偽造 Google Maps 搜尋連結
    3. 嚴禁夾帶連續 8 字以上未翻譯日文假名段落 (容許括號或店名與短料理詞彙如 お通し)
    4. 單店純文字回覆自動執行評分一致性校正

    Returns:
        tuple[str, list[str]]: (經評分校正後之安全文字, 錯誤訊息清單)
    """
    if not response_text or len(response_text.strip()) < 2:
        return response_text, ["追問回覆內容為空或過短"]

    errors: list[str] = []
    sanitized = response_text.strip()

    # 1. 嚴禁偽造 Google Maps 搜尋連結
    markdown_links = re.findall(r"\[([^\]]*)\]\(([^)]+)\)", sanitized)
    for _, url in markdown_links:
        url_lower = url.lower()
        if (
            "google.com/maps/search" in url_lower
            or "google.co.jp/maps/search" in url_lower
            or "maps.google.com/?q=" in url_lower
            or "maps.google.com/maps?q=" in url_lower
        ):
            errors.append("偵測到偽造的 Google Maps 搜尋連結")
            break
    if not errors and (
        "google.com/maps/search" in sanitized.lower() or "google.co.jp/maps/search" in sanitized.lower()
    ):
        errors.append("偵測到偽造的 Google Maps 搜尋連結")

    # 2. 檢查連續 8 字以上之實質日文假名洩漏 (先移除店名與括號內日文原文)
    scrubbed = re.sub(r"[（(][^）)\n]{1,40}[）)]", "", sanitized)
    all_stores = list(stores) + list(extra_allowed_stores or [])
    for s in all_stores:
        for k in ("name_ja", "name", "name_en"):
            val = s.get(k)
            if isinstance(val, str) and val.strip():
                scrubbed = scrubbed.replace(val.strip(), "")
    if re.search(
        r"(?:[\u3040-\u309F\u30A0-\u30FFー]{8,}|(?:[\u3040-\u309F\u30A0-\u30FFー]+[\u4E00-\u9FFF]{1,3}){2,}[\u3040-\u309F\u30A0-\u30FFー]+)",
        scrubbed,
    ):
        errors.append("追問回覆中夾帶未翻譯的生硬日文段落")

    # 3. 單店與多店追問回覆皆進行評分一致性校正
    if len(stores) == 1:
        sanitized = sanitize_single_store_ratings(sanitized, stores[0])
    elif len(stores) >= 2:
        from src.agent.stages.synthesis import sanitize_card_ratings

        sanitized = sanitize_card_ratings(sanitized, stores)

    return sanitized, errors
