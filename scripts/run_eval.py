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

    # 3. 嘗試讀取真實線上對話 Traces (優先 Live DB 1.5s 逾時保護，備援 DuckDB 冷層 Parquet)
    live_runs = None
    live_steps = None
    try:
        from scripts.analyze_traces import fetch_live_data_from_db

        live_runs, live_steps = await asyncio.wait_for(fetch_live_data_from_db(limit=15), timeout=1.5)
    except Exception:
        pass

    # 若 Live DB 未連線，嘗試從冷層 Parquet 讀取
    if not live_runs:
        try:
            import duckdb

            cold_dir = Path("data/cold_archive")
            runs_pattern = str(cold_dir / "**" / "agent_runs_*.parquet")
            steps_pattern = str(cold_dir / "**" / "agent_steps_*.parquet")
            if list(cold_dir.glob("**/agent_runs_*.parquet")):
                con = duckdb.connect()
                recent_sql = f"""
                    SELECT run_id, user_query, status, latency_ms, model_used, total_tokens, created_at
                    FROM '{runs_pattern}'
                    ORDER BY created_at DESC
                    LIMIT 15;
                """
                raw_runs = con.execute(recent_sql).fetchall()
                live_runs = [
                    {
                        "run_id": str(r[0]),
                        "user_query": r[1],
                        "status": r[2],
                        "latency_ms": r[3],
                        "model_used": r[4],
                        "total_tokens": r[5],
                        "created_at": str(r[6]),
                    }
                    for r in raw_runs
                ]
                if live_runs:
                    live_steps = {}
                    r_ids = [r["run_id"] for r in live_runs]
                    ids_clause = ", ".join(f"'{rid}'" for rid in r_ids)
                    steps_sql = f"""
                        SELECT step_id, run_id, step_number, action_type, tool_name, tool_input, tool_output, latency_ms
                        FROM '{steps_pattern}'
                        WHERE run_id IN ({ids_clause})
                        ORDER BY step_number ASC;
                    """
                    raw_steps = con.execute(steps_sql).fetchall()
                    for s in raw_steps:
                        rid = str(s[1])
                        live_steps.setdefault(rid, []).append(
                            {
                                "step_id": str(s[0]),
                                "run_id": rid,
                                "step_number": s[2],
                                "action_type": s[3],
                                "tool_name": s[4],
                                "tool_input": s[5],
                                "tool_output": s[6],
                                "latency_ms": s[7],
                            }
                        )
                con.close()
        except Exception:
            pass

    # 4. 輸出 HTML 儀表板
    html_path = Path(args.output_html)
    generate_html_dashboard(
        summary,
        html_path,
        live_runs=live_runs,
        live_steps_by_run=live_steps,
    )
    print(f"🌐 GitLab Pages HTML 儀表板已生成至: {html_path.resolve()}")

    if summary.all_passed_gate:
        print("\n✅ 所有 4-Tier 硬性門檻皆已達標 (CI Gatekeeper: PASSED)")
        return 0
    else:
        print("\n❌ 未達 4-Tier 硬性門檻 (CI Gatekeeper: FAILED)")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
