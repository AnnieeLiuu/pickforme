import argparse
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb
from src.services.cold_storage import DEFAULT_LOCAL_COLD_DIR


def analyze_cold_traces(cold_dir: Path, custom_glob: Optional[str] = None) -> None:
    runs_pattern = custom_glob or str(cold_dir / "**" / "agent_runs_*.parquet")
    steps_pattern = str(cold_dir / "**" / "agent_steps_*.parquet")

    matching_runs = list(cold_dir.glob("**/agent_runs_*.parquet"))
    if not matching_runs and not custom_glob:
        print(f"⚠️ No Parquet files found in '{cold_dir}'. Run 'python scripts/archive_to_cold.py' first.")
        return

    con = duckdb.connect()
    try:
        # 1. 執行總結分析
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
        if not row or row[0] == 0:
            print("No records found in Parquet files.")
            return

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

        # 2. 模型使用與分佈
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

        # 3. 工具調用分佈 (若有 agent_steps)
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

        print("=" * 65 + "\n")
    finally:
        con.close()


def main() -> None:
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Analyze cold storage Parquet trace files using DuckDB.")
    parser.add_argument(
        "--dir",
        type=str,
        default=str(DEFAULT_LOCAL_COLD_DIR),
        help="Directory containing Parquet files (default: data/cold_archive).",
    )
    args = parser.parse_args()
    analyze_cold_traces(cold_dir=Path(args.dir))


if __name__ == "__main__":
    main()
