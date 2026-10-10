import argparse
import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.eval.evaluator import EvaluationEngine
from src.eval.reporter import generate_html_dashboard, generate_markdown_report


async def main() -> int:
    parser = argparse.ArgumentParser(description="PickForMe 4-Tier 離線評測與 HTML 儀表板生成工具")
    parser.add_argument(
        "--live",
        action="store_true",
        help="啟用真實外部 API/LLM 模式 (預設為 100% 確定性離線回放)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制評測案例數量 (預設跑全部)",
    )
    parser.add_argument(
        "--output-html",
        type=str,
        default="public/index.html",
        help="HTML 儀表板輸出路徑 (預設: public/index.html 供 GitLab Pages 使用)",
    )
    parser.add_argument(
        "--output-md",
        type=str,
        default="docs/eval_report.md",
        help="Markdown 報告輸出路徑 (預設: docs/eval_report.md)",
    )

    args = parser.parse_args()

    print(f"🍱 啟動 PickForMe 4-Tier 評測體系 (模式: {'LIVE (真實API)' if args.live else 'OFFLINE (確定性回放)'})...")

    engine = EvaluationEngine(live_mode=args.live)
    summary = await engine.run_evaluation(limit=args.limit)

    # 1. 輸出終端 Markdown 報告
    md_report = generate_markdown_report(summary)
    print("\n" + md_report)

    # 2. 寫入 Markdown 報告檔案
    md_path = Path(args.output_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_report)
    print(f"📝 Markdown 評測報告已儲存至: {md_path.resolve()}")

    # 3. 輸出 HTML 儀表板
    html_path = Path(args.output_html)
    generate_html_dashboard(summary, html_path)
    print(f"🌐 GitLab Pages HTML 儀表板已生成至: {html_path.resolve()}")

    if summary.all_passed_gate:
        print("\n✅ 所有 4-Tier 硬性門檻皆已達標 (CI Gatekeeper: PASSED)")
        return 0
    else:
        print("\n❌ 未達 4-Tier 硬性門檻 (CI Gatekeeper: FAILED)")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
