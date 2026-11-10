# -*- coding: utf-8 -*-
"""QuickPassGate: 0ms Pre-Classifier Entry Gate & Security Tripwire

負責在進入 Stage 1 LLM 之前，以 0ms 確定性正則規則攔截：
1. Security Tripwire (超長輸入 > 500 字元與典型 Prompt Injection 越獄字串)
2. Healthcheck 探針 (`__healthcheck__`)
3. 純問候與純道謝 (`greeting` / `thanks`)
4. 指定店號追問 (`follow_up_index`: 第1/2/3家...)
5. 純換一批指令 (`pure_more`: 換一批/還有嗎...)
6. 日本國境經緯度直搜 (`coords_search`: lat, lng [cuisine])
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

MAX_INPUT_LENGTH = 500

TRIPWIRE_REPLY = "⚠️ 系統僅提供日本美食與餐廳推薦服務，請輸入您想尋找的日本城市或料理名稱。"
GREETING_REPLY = (
    "您好！我是您的日本美食推薦助手 🍱 請告訴我您想在哪個日本地區尋找什麼料理？（例如：「新宿 拉麵」或「京都 壽喜燒」）"
)
THANKS_REPLY = "不客氣！祝您在日本有美好的用餐體驗 🍽️ 若還想找其他餐廳或換一批推薦，隨時告訴我喔！"

_INJECTION_RE = re.compile(
    r"(?i)(ignore\s+(all\s+)?previous\s+instructions|system\s+prompt|無視先前的指令|忽略上述指令)"
)
_GREETING_RE = re.compile(r"(?i)^(你好|您好|哈囉|嗨|早安|午安|晚安|hi|hello|hey)[！!~～嗎呢\s]*$")
_THANKS_RE = re.compile(r"(?i)^(謝謝|感謝|多謝|謝謝你|太感謝了|thanks|thank\s+you)[！!~～\s]*$")
_FOLLOW_UP_INDEX_RE = re.compile(r"^第?\s*([123一二三])\s*(?:家|間|號店?)(.+)$")
_PURE_MORE_RE = re.compile(r"^(換一批|還有嗎|還有別家嗎|再推薦幾家|下一批|換別家)[？?!！\s]*$")
_COORDS_RE = re.compile(r"^\s*(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*(.*)$")

_INDEX_MAP: dict[str, int] = {
    "1": 1,
    "一": 1,
    "2": 2,
    "二": 2,
    "3": 3,
    "三": 3,
}


@dataclass
class GateResult:
    """QuickPassGate 評估結果"""

    matched: bool
    rule_name: Optional[str] = None
    parsed_intent: Optional[dict[str, Any]] = None
    fast_reply: Optional[str] = None


class QuickPassGate:
    """0ms 確定性入口快篩與安全絆線"""

    def evaluate(self, user_message: str) -> GateResult:
        text = (user_message or "").strip()
        if not text:
            return GateResult(matched=False)

        # 1. Security Tripwire: Length check & Prompt Injection
        if len(text) > MAX_INPUT_LENGTH:
            return GateResult(
                matched=True,
                rule_name="tripwire_length",
                fast_reply=TRIPWIRE_REPLY,
            )

        if _INJECTION_RE.search(text):
            return GateResult(
                matched=True,
                rule_name="tripwire_injection",
                fast_reply=TRIPWIRE_REPLY,
            )

        # 2. Healthcheck probe
        if text == "__healthcheck__":
            return GateResult(
                matched=True,
                rule_name="healthcheck",
                fast_reply="PONG",
            )

        # 3. Pure Greeting / Pure Thanks
        if _GREETING_RE.match(text):
            return GateResult(
                matched=True,
                rule_name="greeting",
                fast_reply=GREETING_REPLY,
            )

        if _THANKS_RE.match(text):
            return GateResult(
                matched=True,
                rule_name="thanks",
                fast_reply=THANKS_REPLY,
            )

        # 4. Explicit Store Follow-up Index (1/2/3 / 一/二/三)
        m_follow = _FOLLOW_UP_INDEX_RE.match(text)
        if m_follow and m_follow.group(2).strip():
            idx = _INDEX_MAP.get(m_follow.group(1), 1)
            return GateResult(
                matched=True,
                rule_name="follow_up_index",
                parsed_intent={
                    "intent": "follow_up",
                    "location": None,
                    "cuisine": None,
                    "modifier": None,
                    "target_index": idx,
                    "question": text,
                },
            )

        # 5. Pure More (不含新修飾語)
        if _PURE_MORE_RE.match(text):
            return GateResult(
                matched=True,
                rule_name="pure_more",
                parsed_intent={
                    "intent": "more",
                    "location": None,
                    "cuisine": None,
                    "modifier": None,
                },
            )

        # 6. Direct Coordinates Search (日本國境座標範圍: 20.0<=lat<=46.0, 122.0<=lng<=154.0)
        m_coords = _COORDS_RE.match(text)
        if m_coords:
            try:
                lat = float(m_coords.group(1))
                lng = float(m_coords.group(2))
                if 20.0 <= lat <= 46.0 and 122.0 <= lng <= 154.0:
                    cuisine = m_coords.group(3).strip() or "熱門人氣美食"
                    return GateResult(
                        matched=True,
                        rule_name="coords_search",
                        parsed_intent={
                            "intent": "search",
                            "location": None,
                            "cuisine": cuisine,
                            "modifier": None,
                            "coords": (lat, lng),
                        },
                    )
            except ValueError:
                pass

        return GateResult(matched=False)
