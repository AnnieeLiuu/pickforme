import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Optional

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb
from src.services.cold_storage import DEFAULT_LOCAL_COLD_DIR


def format_json_block(data_val: Any) -> str:
    """將 input/output 格式化為易讀之 JSON 縮排字串"""
    if data_val is None:
        return "None"
    if isinstance(data_val, str):
        try:
            parsed = json.loads(data_val)
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            return data_val
    elif isinstance(data_val, (dict, list)):
        return json.dumps(data_val, ensure_ascii=False, indent=2)
    return str(data_val)


def inspect_single_run(
    run: dict[str, Any],
    steps: list[dict[str, Any]],
    source_label: str = "Neon DB 熱層",
) -> None:
    """渲染單次問答之完整透視報告 (包含使用者提問、最終卡片、中間各步驟 input/output 與個別耗時)"""
    print("\n" + "=" * 75)
    print("🔬 PickForMe 單次問答端到端歷程透視報告 (Trace Deep-Dive)")
    print("=" * 75)

    run_id = run.get("run_id") or run.get("id")
    query = run.get("user_query", "（無提問文字）")
    status = run.get("status", "unknown")
    latency = run.get("latency_ms", 0)
    model = run.get("model_used") or "N/A"
    tokens = run.get("total_tokens") or 0
    created_at = run.get("created_at", "N/A")

    print(f"🆔 Run ID      : {run_id}")
    print(f"📍 歷程來源    : {source_label}")
    print(f"⏰ 提問時間    : {created_at}")
    print(f"🎯 提問提示句  : {query}")
    print(f"⚡ 總體耗時    : {latency:,} ms")
    print(f"🧠 調用模型    : {model}")
    print(f"🪙 消耗 Token  : {tokens:,}")
    print(f"📌 執行狀態    : {status}")
    print("-" * 75)

    # 1. 提取最終生成的卡片內容 (從 State 4 或 follow_up / chitchat 步驟的 tool_output)
    final_card = None
    for step in reversed(steps):
        out_raw = step.get("tool_output")
        if out_raw:
            if isinstance(out_raw, str):
                try:
                    out_raw = json.loads(out_raw)
                except Exception:
                    pass
            if isinstance(out_raw, dict) and out_raw.get("card_content"):
                final_card = out_raw.get("card_content")
                break

    if final_card:
        print("\n🍱 【最終生成回答卡片 (Final Response Card)】:")
        print("```markdown")
        print(final_card)
        print("```")
    else:
        print("\n🍱 【最終生成回答卡片】: （此 Run 未捕獲到最終卡片文字）")

    print("-" * 75)
    print("🔄 【中間流程管線節點概覽 (Pipeline Steps Timeline)】:")
    print(f"{'步驟':<6} | {'階段 (Action)':<22} | {'調用工具 (Tool)':<26} | {'個別耗時':<10} | {'佔比':<6}")
    print("-" * 75)

    total_step_lat = sum(s.get("latency_ms") or 0 for s in steps)
    for s in steps:
        step_num = s.get("step_number", 0)
        action = s.get("action_type") or "N/A"
        tool = s.get("tool_name") or "-"
        step_lat = s.get("latency_ms") or 0
        pct = (step_lat / total_step_lat * 100) if total_step_lat > 0 else 0.0
        print(f"#{step_num:<5} | {action:<22} | {tool:<26} | {step_lat:>6} ms | {pct:>5.1f}%")

    print("-" * 75)
    print("🔍 【各流程階段詳細 Input / Output 探勘】:")
    for s in steps:
        step_num = s.get("step_number", 0)
        action = s.get("action_type") or "N/A"
        tool = s.get("tool_name") or "-"
        step_lat = s.get("latency_ms") or 0

        print(f"\n▶ 步驟 #{step_num}：階段「{action}」| 工具「{tool}」| 耗時 {step_lat} ms")
        print("  📥 [Input 輸入]:")
        in_str = format_json_block(s.get("tool_input"))
        for line in in_str.splitlines():
            print(f"     {line}")

        print("  📤 [Output 輸出]:")
        out_val = s.get("tool_output")
        # 若是 State 4，為保持排版精確，截短卡片只印預覽，其餘完整印出
        if isinstance(out_val, str):
            try:
                out_val = json.loads(out_val)
            except Exception:
                pass
        if isinstance(out_val, dict) and "card_content" in out_val:
            preview_dict = {k: v for k, v in out_val.items() if k != "card_content"}
            preview_dict["card_content_preview"] = (
                out_val["card_content"][:120] + "... (已於上方完整展示)"
                if len(out_val["card_content"]) > 120
                else out_val["card_content"]
            )
            out_str = json.dumps(preview_dict, ensure_ascii=False, indent=2)
        else:
            out_str = format_json_block(out_val)

        for line in out_str.splitlines():
            print(f"     {line}")

    print("=" * 75 + "\n")


def display_recent_runs_table(runs: list[dict[str, Any]], source_label: str = "Neon DB 熱層") -> None:
    """印出近期問答對話清單表格"""
    print(f"\n📋 近期問答對話紀錄清單 [來源: {source_label}] (Recent Q&A Runs):")
    print(f"{'序號':<5} | {'時間':<19} | {'耗時':<8} | {'狀態':<9} | {'提問提示句 (User Query)':<35}")
    print("-" * 85)
    for idx, r in enumerate(runs, 1):
        created_str = str(r.get("created_at", "N/A"))[:19]
        lat_str = f"{r.get('latency_ms') or 0}ms"
        status_str = r.get("status") or "unknown"
        query_str = (r.get("user_query") or "無文字").replace("\n", " ")
        if len(query_str) > 32:
            query_str = query_str[:30] + "..."
        print(f"[{idx:<2}] | {created_str:<19} | {lat_str:<8} | {status_str:<9} | {query_str:<35}")
    print("-" * 85)


async def fetch_live_data_from_db(limit: int = 15) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """從 Neon Postgres 熱層直接讀取最近問答與步驟資料 (尚未歸檔 Parquet 時之 live 檢視)"""
    from src.db.connection import get_db_pool

    pool = await get_db_pool()
    runs: list[dict[str, Any]] = []
    steps_by_run: dict[str, list[dict[str, Any]]] = {}

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT run_id, chat_id, user_query, model_used, total_tokens, latency_ms, status, created_at
                FROM agent_runs
                ORDER BY created_at DESC
                LIMIT %s;
                """,
                (limit,),
            )
            raw_runs = await cur.fetchall()
            run_ids = []
            for r in raw_runs:
                if isinstance(r, dict):
                    rd = {
                        "run_id": str(r["run_id"]),
                        "chat_id": r.get("chat_id"),
                        "user_query": r.get("user_query"),
                        "model_used": r.get("model_used"),
                        "total_tokens": r.get("total_tokens"),
                        "latency_ms": r.get("latency_ms"),
                        "status": r.get("status"),
                        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                    }
                else:
                    rd = {
                        "run_id": str(r[0]),
                        "chat_id": r[1],
                        "user_query": r[2],
                        "model_used": r[3],
                        "total_tokens": r[4],
                        "latency_ms": r[5],
                        "status": r[6],
                        "created_at": r[7].isoformat() if r[7] else None,
                    }
                runs.append(rd)
                run_ids.append(rd["run_id"])

            if run_ids:
                await cur.execute(
                    """
                    SELECT step_id, run_id, step_number, action_type, tool_name, tool_input, tool_output, latency_ms, error_message, created_at
                    FROM agent_steps
                    WHERE run_id = ANY(%s)
                    ORDER BY step_number ASC;
                    """,
                    (run_ids,),
                )
                raw_steps = await cur.fetchall()
                for s in raw_steps:
                    if isinstance(s, dict):
                        rid = str(s["run_id"])
                        sd = {
                            "step_id": str(s["step_id"]),
                            "run_id": rid,
                            "step_number": s.get("step_number"),
                            "action_type": s.get("action_type"),
                            "tool_name": s.get("tool_name"),
                            "tool_input": s.get("tool_input"),
                            "tool_output": s.get("tool_output"),
                            "latency_ms": s.get("latency_ms"),
                            "error_message": s.get("error_message"),
                            "created_at": s["created_at"].isoformat() if s.get("created_at") else None,
                        }
                    else:
                        rid = str(s[1])
                        sd = {
                            "step_id": str(s[0]),
                            "run_id": rid,
                            "step_number": s[2],
                            "action_type": s[3],
                            "tool_name": s[4],
                            "tool_input": s[5],
                            "tool_output": s[6],
                            "latency_ms": s[7],
                            "error_message": s[8],
                            "created_at": s[9].isoformat() if s[9] else None,
                        }
                    steps_by_run.setdefault(rid, []).append(sd)

    return runs, steps_by_run


def analyze_with_duckdb(
    cold_dir: Path,
    inspect_idx: Optional[int] = None,
    inspect_id: Optional[str] = None,
    inspect_latest: bool = False,
    live_mode: bool = False,
    cold_only: bool = False,
) -> None:
    runs_parquet_files = list(cold_dir.glob("**/agent_runs_*.parquet"))
    runs_pattern = str(cold_dir / "**" / "agent_runs_*.parquet")
    steps_pattern = str(cold_dir / "**" / "agent_steps_*.parquet")

    # 1. 若有冷層 Parquet 檔案，先以 DuckDB 印出 OLAP 大數據分析摘要
    if runs_parquet_files:
        con = duckdb.connect()
        try:
            summary_sql = f"""
                SELECT
                    COUNT(*) AS total_runs,
                    COUNT(CASE WHEN status = 'completed' THEN 1 END) AS successful,
                    COUNT(CASE WHEN status = 'failed' THEN 1 END) AS failed,
                    ROUND(AVG(latency_ms), 1) AS avg_lat,
                    ROUND(QUANTILE_CONT(latency_ms, 0.5), 1) AS p50_lat,
                    ROUND(QUANTILE_CONT(latency_ms, 0.95), 1) AS p95_lat,
                    MAX(latency_ms) AS max_lat,
                    SUM(COALESCE(total_tokens, 0)) AS tokens
                FROM '{runs_pattern}';
            """
            row = con.execute(summary_sql).fetchone()
            if row and row[0] > 0:
                total, success, failed, avg_lat, p50, p95, max_lat, tokens = row
                success_rate = (success / total * 100) if total > 0 else 100.0

                print("\n" + "=" * 65)
                print("📊 PickForMe Cold Storage DuckDB OLAP Analytics Report")
                print("=" * 65)
                print(f"📁 Source Directory : {cold_dir}")
                print(f"📈 Total Runs       : {total:,}")
                print(f"✅ Successful Runs  : {success:,} ({success_rate:.1f}%)")
                print(f"❌ Failed Runs      : {failed:,}")
                print(f"⚡ Latency Metrics   : Avg {avg_lat}ms | P50 {p50}ms | P95 {p95}ms | Max {max_lat}ms")
                print(f"🪙 Total Tokens Used: {tokens:,}")
                print("-" * 65)

                models_sql = f"""
                    SELECT
                        COALESCE(model_used, 'unknown') AS model,
                        COUNT(*) AS calls,
                        ROUND(COUNT(*) * 100.0 / {total}, 1) AS call_pct,
                        SUM(COALESCE(total_tokens, 0)) AS model_tokens,
                        ROUND(AVG(latency_ms), 1) AS avg_lat
                    FROM '{runs_pattern}'
                    GROUP BY model_used
                    ORDER BY calls DESC;
                """
                model_rows = con.execute(models_sql).fetchall()
                print("\n🧠 LLM Models Breakdown:")
                print(f"{'Model':<30} | {'Calls':<8} | {'Share':<6} | {'Tokens':<10} | {'Avg Latency':<12}")
                print("-" * 75)
                for mr in model_rows:
                    print(f"{mr[0]:<30} | {mr[1]:<8} | {mr[2]:>5.1f}% | {mr[3]:<10,} | {mr[4]}ms")

                matching_steps = list(cold_dir.glob("**/agent_steps_*.parquet"))
                if matching_steps:
                    steps_sql = f"""
                        SELECT
                            COALESCE(tool_name, action_type) AS tool,
                            COUNT(*) AS executions,
                            ROUND(AVG(latency_ms), 1) AS avg_lat
                        FROM '{steps_pattern}'
                        GROUP BY tool
                        ORDER BY executions DESC;
                    """
                    step_rows = con.execute(steps_sql).fetchall()
                    print("\n🔧 Tool & Action Execution Breakdown:")
                    print(f"{'Tool / Action':<35} | {'Executions':<12} | {'Avg Latency':<12}")
                    print("-" * 65)
                    for sr in step_rows:
                        print(f"{sr[0]:<35} | {sr[1]:<12} | {sr[2]}ms")
        finally:
            con.close()

    # 2. 決定「近期對話清單」與「透視對象」的資料來源 (預設優先讀取 Neon DB 即時熱層，讓剛測完的問答立即可見)
    use_live_db = not cold_only
    live_runs: list[dict[str, Any]] = []
    live_steps: dict[str, list[dict[str, Any]]] = {}

    if use_live_db:
        try:
            live_runs, live_steps = asyncio.run(fetch_live_data_from_db())
        except Exception as e:
            if live_mode:
                print(f"❌ 連線 Neon DB 讀取即時歷程失敗: {e}")
                return
            # 靜默降級至冷層 Parquet
            pass

    if live_runs:
        _handle_inspection_flow(
            runs=live_runs,
            steps_by_run=live_steps,
            inspect_latest=inspect_latest,
            inspect_idx=inspect_idx,
            inspect_id=inspect_id,
            source_label="Neon DB 熱層 (Live)",
        )
        return

    # 若未連上 Live DB 或指定 --cold，則從 Parquet 讀取最近問答 runs
    if not runs_parquet_files:
        print(
            f"⚠️ 在 '{cold_dir}' 暫無 Parquet 冷層檔案，且無法連線 Neon DB。請確認網路連線或執行 'archive_to_cold.py'！"
        )
        return

    con = duckdb.connect()
    try:
        recent_runs_sql = f"""
            SELECT run_id, user_query, status, latency_ms, model_used, total_tokens, created_at
            FROM '{runs_pattern}'
            ORDER BY created_at DESC
            LIMIT 15;
        """
        raw_runs = con.execute(recent_runs_sql).fetchall()
        cold_runs = [
            {
                "run_id": str(r[0]),
                "user_query": r[1],
                "status": r[2],
                "latency_ms": r[3],
                "model_used": r[4],
                "total_tokens": r[5],
                "created_at": r[6],
            }
            for r in raw_runs
        ]

        cold_steps_by_run: dict[str, list[dict[str, Any]]] = {}
        if cold_runs:
            r_ids = [r["run_id"] for r in cold_runs]
            ids_clause = ", ".join(f"'{rid}'" for rid in r_ids)
            steps_detail_sql = f"""
                SELECT step_id, run_id, step_number, action_type, tool_name, tool_input, tool_output, latency_ms, error_message, created_at
                FROM '{steps_pattern}'
                WHERE run_id IN ({ids_clause})
                ORDER BY step_number ASC;
            """
            raw_steps = con.execute(steps_detail_sql).fetchall()
            for s in raw_steps:
                rid = str(s[1])
                sd = {
                    "step_id": str(s[0]),
                    "run_id": rid,
                    "step_number": s[2],
                    "action_type": s[3],
                    "tool_name": s[4],
                    "tool_input": s[5],
                    "tool_output": s[6],
                    "latency_ms": s[7],
                    "error_message": s[8],
                    "created_at": s[9],
                }
                cold_steps_by_run.setdefault(rid, []).append(sd)

        _handle_inspection_flow(
            runs=cold_runs,
            steps_by_run=cold_steps_by_run,
            inspect_latest=inspect_latest,
            inspect_idx=inspect_idx,
            inspect_id=inspect_id,
            source_label=f"Parquet 冷層 ({cold_dir})",
        )
    finally:
        con.close()


def _handle_inspection_flow(
    runs: list[dict[str, Any]],
    steps_by_run: dict[str, list[dict[str, Any]]],
    inspect_latest: bool,
    inspect_idx: Optional[int],
    inspect_id: Optional[str],
    source_label: str = "Neon DB 熱層",
) -> None:
    """處理互動式選單或直接透視指定 Run"""
    if inspect_latest and runs:
        chosen_run = runs[0]
        inspect_single_run(chosen_run, steps_by_run.get(chosen_run["run_id"], []), source_label=source_label)
        return

    if inspect_id:
        target = next((r for r in runs if r["run_id"] == inspect_id), None)
        if target:
            inspect_single_run(target, steps_by_run.get(target["run_id"], []), source_label=source_label)
        else:
            print(f"❌ 查無此 Run ID: {inspect_id}")
        return

    if inspect_idx is not None and 1 <= inspect_idx <= len(runs):
        chosen_run = runs[inspect_idx - 1]
        inspect_single_run(chosen_run, steps_by_run.get(chosen_run["run_id"], []), source_label=source_label)
        return

    # 預設印出清單，並依終端機環境提供互動選擇
    display_recent_runs_table(runs, source_label=source_label)

    if sys.stdin.isatty():
        try:
            choice = input("\n👉 請輸入要透視的問答序號 [1-%d] (或輸入 q 結束): " % len(runs)).strip()
            if choice.lower() != "q" and choice.isdigit():
                idx_val = int(choice)
                if 1 <= idx_val <= len(runs):
                    target_run = runs[idx_val - 1]
                    inspect_single_run(
                        target_run, steps_by_run.get(target_run["run_id"], []), source_label=source_label
                    )
                else:
                    print("⚠️ 輸入序號超出範圍。")
        except (EOFError, KeyboardInterrupt):
            pass


def main() -> None:
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="PickForMe Trace OLAP & End-to-End Pipeline Analysis using DuckDB / Neon DB."
    )
    parser.add_argument(
        "--dir",
        type=str,
        default=str(DEFAULT_LOCAL_COLD_DIR),
        help="Directory containing Parquet files (default: data/cold_archive).",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Directly drill down and display the most recent Q&A run.",
    )
    parser.add_argument(
        "--inspect",
        type=int,
        default=None,
        help="Index of the run from the recent list to drill down into (1-indexed).",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Specific UUID run_id to inspect.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Force inspecting live runs directly from Neon Postgres hot tier.",
    )
    parser.add_argument(
        "--cold",
        action="store_true",
        help="Force inspecting archived runs from Parquet cold files only.",
    )

    args = parser.parse_args()
    analyze_with_duckdb(
        cold_dir=Path(args.dir),
        inspect_idx=args.inspect,
        inspect_id=args.run_id,
        inspect_latest=args.latest,
        live_mode=args.live,
        cold_only=args.cold,
    )


if __name__ == "__main__":
    main()
