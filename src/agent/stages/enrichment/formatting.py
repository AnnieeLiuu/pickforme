# -*- coding: utf-8 -*-
"""Payment and store attribute formatting utilities for Enrichment stage."""

from __future__ import annotations

from typing import Optional


def format_payment_methods(
    supports_paypay: Optional[bool] = None,
    credit_cards: Optional[list[str]] = None,
    accepts_credit_cards: Optional[bool] = None,
    accepts_cash_only: Optional[bool] = None,
    accepts_nfc: Optional[bool] = None,
) -> str:
    """將 Google Places (New) 與 Tabelog 支付方式雙源融合轉換為旅客友善的說明字串 (消除假陰性武斷斷言)"""
    parts: list[str] = []

    # 1. 信用卡與無現金支付判定：
    #    (a) 若 Tabelog 有明確卡別優先展示卡別
    if credit_cards and len(credit_cards) > 0:
        if credit_cards == ["可"]:
            parts.append("信用卡")
        else:
            cards_str = "、".join(credit_cards[:3])
            parts.append(f"信用卡 ({cards_str})")
    #    (b) 若 Google 標記可刷卡亦採信
    elif accepts_credit_cards is True:
        parts.append("信用卡")
    #    (c) 若 Google Places 明確標註非僅收現金 (acceptsCashOnly: False)，且未被 Tabelog 明確否定為 0 卡
    elif accepts_cash_only is False and (credit_cards is None or len(credit_cards) > 0):
        parts.append("信用卡 / 電子支付")

    # 2. 感應支付 (NFC / Apple Pay 等) 判定
    if accepts_nfc is True and "信用卡 / 電子支付" not in parts:
        parts.append("感應支付")

    # 3. PayPay 行動支付判定
    if supports_paypay is True:
        parts.append("PayPay")

    if parts:
        return "可使用 " + " / ".join(parts)

    # 4. 現金判定：必須有明確信號指出「僅收現金」
    if accepts_cash_only is True or (credit_cards is not None and len(credit_cards) == 0 and supports_paypay is False):
        return "現金為主 (不支援信用卡/行動支付，請備足現鈔)"

    # 5. 未知/未明確標記：溫和提示
    if supports_paypay is False:
        return "現金為主 (不支援 PayPay，建議備足日圓現鈔)"

    return "現金為主 (建議備足日圓現鈔)"
