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
