# -*- coding: utf-8 -*-
"""Deterministic Progressive Skill Loader

以零額外 LLM Round-trip 的方式依場景動態組裝提示詞與在地飲食攻略 Skill：
- 核心人格 (`config/prompts/agent_system.md`)
- 場景技能 (`config/skills/scenarios/*.md`)
- 飲食文化攻略 (`config/skills/etiquette/*.md`，依 triggers 動態索引，平常 0 Token)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_AGENT_SYSTEM_PATH = _PROJECT_ROOT / "config" / "prompts" / "agent_system.md"
DEFAULT_SKILLS_DIR = _PROJECT_ROOT / "config" / "skills"

_TRIGGER_COMMENT_RE = re.compile(r"<!--\s*triggers\s*:\s*([^>]+?)\s*-->", re.IGNORECASE)


class SkillLoader:
    """漸進式提示詞與在地文化 Skill 載入器 (支援記憶體快取與 Fail-Open 保底)"""

    def __init__(
        self,
        agent_system_path: Optional[Path] = None,
        skills_dir: Optional[Path] = None,
    ) -> None:
        self.agent_system_path = agent_system_path or DEFAULT_AGENT_SYSTEM_PATH
        self.skills_dir = skills_dir or DEFAULT_SKILLS_DIR
        self._text_cache: dict[Path, str] = {}
        self._etiquette_index: Optional[list[tuple[tuple[str, ...], str]]] = None

    def reload(self) -> None:
        """清除快取以便重新載入磁碟上的 Skill 內容"""
        self._text_cache.clear()
        self._etiquette_index = None

    def _read_cached(self, path: Path) -> Optional[str]:
        if path in self._text_cache:
            return self._text_cache[path]
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8").strip()
            self._text_cache[path] = text
            return text
        except Exception as e:
            logger.warning(f"Failed to read skill file {path}: {e}")
            return None

    def load_core_persona(self, fallback: str = "") -> str:
        """載入精簡共用核心人格 (agent_system.md)"""
        text = self._read_cached(self.agent_system_path)
        return text if text is not None else fallback

    def load_scenario(self, name: str, fallback: str = "", **placeholders: object) -> str:
        """載入場景專屬 Skill (config/skills/scenarios/{name}.md)，支援安全佔位符替換"""
        path = self.skills_dir / "scenarios" / f"{name}.md"
        text = self._read_cached(path)
        if text is None:
            text = fallback
        if text and placeholders:
            for key, val in placeholders.items():
                text = text.replace(f"{{{key}}}", str(val))
        return text

    def _build_etiquette_index(self) -> list[tuple[tuple[str, ...], str]]:
        if self._etiquette_index is not None:
            return self._etiquette_index

        entries: list[tuple[tuple[str, ...], str]] = []
        etiquette_dir = self.skills_dir / "etiquette"
        if etiquette_dir.exists() and etiquette_dir.is_dir():
            for md_file in sorted(etiquette_dir.glob("*.md")):
                raw = self._read_cached(md_file)
                if not raw:
                    continue
                match = _TRIGGER_COMMENT_RE.search(raw)
                if not match:
                    continue
                triggers = tuple(t.strip().lower() for t in match.group(1).split(",") if t.strip())
                body = _TRIGGER_COMMENT_RE.sub("", raw, count=1).strip()
                if triggers and body:
                    entries.append((triggers, body))

        self._etiquette_index = entries
        return entries

    def match_etiquette_skills(
        self,
        cuisine: str = "",
        location_name: str = "",
        user_query: str = "",
        max_skills: int = 2,
    ) -> list[str]:
        """依 cuisine / 地點 / 使用者問句匹配飲食文化攻略 Skill (最多 max_skills 份)"""
        if max_skills <= 0:
            return []
        haystack = f"{cuisine} {location_name} {user_query}".lower().strip()
        if not haystack:
            return []

        matched: list[str] = []
        for triggers, body in self._build_etiquette_index():
            if any(t in haystack for t in triggers):
                matched.append(body)
                if len(matched) >= max_skills:
                    break
        return matched

    def build_synthesis_system_prompt(
        self,
        resolved_category: str,
        store_count: int,
        location_name: Optional[str],
        cuisine: str = "",
        user_query: str = "",
        is_wide_area: bool = False,
        fallback_card_prompt: str = "",
    ) -> str:
        """動態組裝 State 4 推薦卡片之模組化 System Prompt"""
        parts: list[str] = []

        core = self.load_core_persona()
        card_rules = self.load_scenario("card-synthesis")
        if core and card_rules:
            parts.append(core)
            parts.append(card_rules)
        elif card_rules:
            parts.append(card_rules)
        elif core:
            parts.append(core)
        elif fallback_card_prompt:
            parts.append(fallback_card_prompt)

        if resolved_category == "drink":
            drink_skill = self.load_scenario("category-drink")
            if drink_skill:
                parts.append(drink_skill)
        elif resolved_category == "souvenir":
            souvenir_skill = self.load_scenario("category-souvenir")
            if souvenir_skill:
                parts.append(souvenir_skill)

        if store_count < 3:
            honest_label = (
                "特色名產／伴手禮名店"
                if resolved_category == "souvenir"
                else ("特色茶房／名店" if resolved_category == "drink" else "高評價店家")
            )
            sparse_skill = self.load_scenario(
                "sparse-results",
                store_count=store_count,
                honest_label=honest_label,
            )
            if sparse_skill:
                parts.append(sparse_skill)

        parts.append(
            "【精準交通與步行導航指令】\n"
            "店家資料所提供之「步行距離」（例如「從『西武新宿站』步行約 3 分鐘」、「位於新宿『歌舞伎町』 (距定位點約 557m)」），"
            "請務必 100% 忠實完整引用，絕對禁止自行簡化或竄改為模糊的大區域出發（如嚴禁重寫為「從『新宿』步行」）！"
        )

        if is_wide_area and location_name:
            wide_skill = self.load_scenario("wide-area-nav", location_name=location_name)
            if wide_skill:
                parts.append(wide_skill)

        etiquette_skills = self.match_etiquette_skills(
            cuisine=cuisine,
            location_name=location_name or "",
            user_query=user_query,
        )
        parts.extend(etiquette_skills)

        return "\n\n".join(p for p in parts if p)

    def build_follow_up_system_prompt(self, is_comparison: bool = False) -> str:
        """組裝追問分支 System Prompt (完全隔離 8 欄推薦卡片模板)"""
        core = self.load_core_persona(fallback="你是一位專業、親切的日本美食特派員「PickForMe」。")
        if is_comparison:
            fallback_comp = (
                "使用者正在要求對比上一輪推薦名單中的多家店家。請進行條理分明的多面向比較：\n"
                "1. 🍱 風味特色與口感差異\n"
                "2. 💰 預算價位與 CP 值\n"
                "3. 🚶 交通步行與排隊狀況\n"
                "4. 💡 總結推薦：針對不同情境明確建議選哪一家。"
            )
            scenario = self.load_scenario("follow-up-comparison", fallback=fallback_comp)
        else:
            fallback_qa = (
                "使用者正在追問上一輪推薦名單中的特定店家。請根據所提供的目標店家資料，簡潔、親切地直接回答單一問題。"
            )
            scenario = self.load_scenario("follow-up-qa", fallback=fallback_qa)
        return f"{core}\n\n{scenario}".strip()


_default_loader = SkillLoader()


def get_skill_loader() -> SkillLoader:
    """取得模組共用 SkillLoader 單例"""
    return _default_loader
