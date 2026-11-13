# -*- coding: utf-8 -*-
"""Mermaid Documentation Syntax & Rendering Guardrail Test

自動化掃描所有文檔 Markdown 檔案中的 ```mermaid 區塊，
嚴格防禦未加引號括號造成的 Mermaid 解析器崩潰 (Parse error on line XX: ... got 'PS')，
確保所有架構圖與狀態機流程圖在 GitHub / GitLab / VS Code 中 100% 正常渲染。
"""

import re
from pathlib import Path


def test_markdown_mermaid_diagrams_syntax() -> None:
    """驗證全專案 Markdown 文檔內所有 Mermaid 圖表語法合規：

    1. 邊緣標籤 (Edge Labels): 若包含括號或特殊字元 (如 `|prewarm_jobs (SKIP LOCKED)|`)，
       必須以雙引號包裹 (如 `|"prewarm_jobs (SKIP LOCKED)"|`)，杜絕 Mermaid 語法解析中斷。
    2. 子圖配對 (Subgraph Pairing): `subgraph` 與 `end` 數量必須精準閉合。
    3. 時序圖區塊 (Sequence Blocks): `alt/opt/loop/par/critical` 與 `end` 必須精準閉合。
    """
    root_dir = Path(__file__).parent.parent
    md_files = list(root_dir.glob("**/*.md"))

    violations: list[str] = []

    for md_file in md_files:
        rel_path = md_file.relative_to(root_dir)
        # 排除虛擬環境與快取
        if any(part.startswith(".") for part in rel_path.parts):
            continue

        content = md_file.read_text(encoding="utf-8")
        mermaid_matches = re.finditer(r"```mermaid\s+(.*?)\s+```", content, re.DOTALL)

        for block_idx, match in enumerate(mermaid_matches, 1):
            block_content = match.group(1).strip()
            lines = block_content.split("\n")
            first_line = lines[0].strip() if lines else ""

            is_sequence = first_line.startswith("sequenceDiagram")

            subgraph_count = 0
            seq_block_count = 0
            end_count = 0

            for line_no, line in enumerate(lines, 1):
                stripped = line.strip()
                if not is_sequence:
                    if stripped.startswith("subgraph"):
                        subgraph_count += 1
                    elif stripped == "end":
                        end_count += 1
                else:
                    if any(stripped.startswith(kw) for kw in ["alt ", "opt ", "loop ", "par ", "critical "]):
                        seq_block_count += 1
                    elif stripped == "end":
                        end_count += 1

                # 檢查邊緣標籤 (Edge Labels) 是否存在未加引號的括號/特殊字元
                edge_match = re.search(r"\|([^\"\|\n]+)\|", line)
                if edge_match:
                    label = edge_match.group(1).strip()
                    if any(c in label for c in "()[]{}"):
                        violations.append(
                            f"{rel_path} (區塊 #{block_idx}, 第 {line_no} 行): "
                            f'邊緣標籤含有未以雙引號包裹的特殊字元: |{label}| -> 應改為 |"{label}"|'
                        )

            if not is_sequence and subgraph_count != end_count:
                violations.append(
                    f"{rel_path} (區塊 #{block_idx}): subgraph 數量 ({subgraph_count}) 與 end 數量 ({end_count}) 不一致"
                )
            elif is_sequence and seq_block_count != end_count:
                violations.append(
                    f"{rel_path} (區塊 #{block_idx}): 時序區塊數量 ({seq_block_count}) 與 end 數量 ({end_count}) 不一致"
                )

    assert not violations, "發現 Mermaid 文檔語法錯誤：\n" + "\n".join(f"  ❌ {v}" for v in violations)


def test_architecture_fsm_mermaid_semantic_consistency() -> None:
    """驗證 ARCHITECTURE.md 核心 FSM 流程圖之狀態流轉與分支語意完整性。

    徹底防範「程式碼改了邏輯、文件只補條列文字、卻漏改核心 Mermaid 流程圖」之文檔漂移：
    1. 必須涵蓋 4 個核心 State 子圖 (State1 ~ State4)
    2. State 1: 必須包含 QuickPass、文化探索分支 (explore_cuisine / explore_category) 與地理場景 1 (is_prefecture_level_area)
    3. State 2: 必須包含地理場景 2 (is_wide_area_origin / 景區模式) 與場景 3 (都會模式)
    4. State 3: 必須包含最近車站萃取 (STATION_WALK_PATTERN)、實體比對與硬性條件感測 (ConstraintSensor)
    5. State 4: 必須包含消費品類三分支自適應 (resolve_card_category / food / drink / souvenir) 與 Scoped Anchor 護欄
    """
    root_dir = Path(__file__).parent.parent
    arch_file = root_dir / "docs" / "ARCHITECTURE.md"
    assert arch_file.exists(), "docs/ARCHITECTURE.md 不存在"

    content = arch_file.read_text(encoding="utf-8")

    # 提取 Section D 的 Mermaid 流程圖區塊
    mermaid_match = re.search(r"### D\. 完整 Agent 決策.*?```mermaid\s+(flowchart TD.*?)\s+```", content, re.DOTALL)
    assert mermaid_match is not None, "docs/ARCHITECTURE.md 缺少 Section D 的 flowchart TD 流程圖"

    fsm_diagram = mermaid_match.group(1)

    required_keywords = [
        # 4 大狀態機子圖
        "subgraph State1",
        "subgraph State2",
        "subgraph State3",
        "subgraph State4",
        # State 1 核心分支：快篩、探索導覽（吃/喝/伴手禮）、廣域模糊動態收斂
        "QuickPass",
        "explore_cuisine",
        "explore_category",
        "is_location_ambiguous",
        # State 2 核心分支：地理三場景分流 (度假景區 vs 都會商圈)
        "is_wide_area_origin",
        "ScenicLadder",
        "UrbanLadder",
        # State 3 核心節點：最近車站提取、實體比對、硬性條件感測
        "STATION_WALK_PATTERN",
        "EntityResolver",
        "ConstraintSensor",
        "AlignWalk",
        # State 4 核心節點：三大品類自適應 (吃的/喝的/伴手禮)、Scoped Anchor、模板回退
        "resolve_card_category",
        "food",
        "drink",
        "souvenir",
        "GuardrailCheck",
        "TemplateFallback",
    ]

    missing = [kw for kw in required_keywords if kw not in fsm_diagram]
    assert not missing, (
        "docs/ARCHITECTURE.md Section D Mermaid 流程圖缺少以下關鍵狀態/決策節點，發生流程圖漂移：\n"
        + "\n".join(f"  ❌ 缺少關鍵字: {m}" for m in missing)
    )
