import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from src.config import settings
from src.db.connection import get_db_pool

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_COLD_DIR = Path("data/cold_archive")


class ColdStorageArchiver:
    """冷熱資料儲存分層歸檔服務 (Hot/Cold Tiering & DuckDB OLAP)

    將 Neon Postgres 中超過保留天數的歷史 Trace (agent_runs, agent_steps)
    轉檔壓縮為標準 Apache Parquet，支援本機與 GCS 雙模冷層儲存，
    並於歸檔完成後清理熱層，嚴守 Neon 1GB 容量防線。
    """

    def __init__(
        self,
        local_dir: Optional[Path] = None,
        bucket_name: Optional[str] = None,
    ):
        self.local_dir = local_dir or DEFAULT_LOCAL_COLD_DIR
        self.bucket_name = bucket_name or settings.GCS_BUCKET_NAME

    def _try_upload_to_gcs(self, local_file_path: Path, gcs_blob_name: str) -> bool:
        """嘗試上傳 Parquet 檔案至 Google Cloud Storage (若具備 GCP 憑證)"""
        try:
            from google.cloud import storage  # type: ignore

            client = storage.Client()
            bucket = client.bucket(self.bucket_name)
            blob = bucket.blob(gcs_blob_name)
            blob.upload_from_filename(str(local_file_path))
            logger.info(f"Successfully uploaded {local_file_path} to gs://{self.bucket_name}/{gcs_blob_name}")
            return True
        except Exception as e:
            logger.warning(f"GCS upload unavailable or failed ({e}); falling back to local cold storage.")
            return False

    async def archive_traces(
        self,
        retention_days: int = 14,
        delete_after_archive: bool = True,
    ) -> dict[str, Any]:
        """執行歷史 Trace 歸檔作業"""
        pool = await get_db_pool()
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")

        year_dir = self.local_dir / f"year={now.year}" / f"month={now.month:02d}"
        year_dir.mkdir(parents=True, exist_ok=True)

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # 1. 查詢超過保留週期的 agent_runs
                query_runs = """
                    SELECT run_id, chat_id, user_query, model_used, total_tokens, latency_ms, status, created_at
                    FROM agent_runs
                    WHERE created_at < NOW() - make_interval(days => %s)
                    ORDER BY created_at ASC;
                """
                await cur.execute(query_runs, (retention_days,))
                raw_runs = await cur.fetchall()

                if not raw_runs:
                    return {
                        "status": "skipped",
                        "message": f"No traces older than {retention_days} days found.",
                        "runs_archived": 0,
                        "steps_archived": 0,
                        "parquet_files": [],
                        "destination": "none",
                    }

                runs_data: list[dict[str, Any]] = []
                expired_run_ids: list[str] = []
                for r in raw_runs:
                    d = (
                        dict(r)
                        if isinstance(r, dict)
                        else {
                            "run_id": str(r[0]),
                            "chat_id": r[1],
                            "user_query": r[2],
                            "model_used": r[3],
                            "total_tokens": r[4],
                            "latency_ms": r[5],
                            "status": r[6],
                            "created_at": r[7].isoformat() if r[7] else None,
                        }
                    )
                    d["run_id"] = str(d["run_id"])
                    if isinstance(d.get("created_at"), datetime):
                        d["created_at"] = d["created_at"].isoformat()
                    runs_data.append(d)
                    expired_run_ids.append(d["run_id"])

                # 2. 查詢關聯之 agent_steps
                query_steps = """
                    SELECT step_id, run_id, step_number, action_type, tool_name, tool_input, tool_output, latency_ms, error_message, created_at
                    FROM agent_steps
                    WHERE run_id = ANY(%s)
                    ORDER BY step_number ASC;
                """
                await cur.execute(query_steps, (expired_run_ids,))
                raw_steps = await cur.fetchall()

                steps_data: list[dict[str, Any]] = []
                for s in raw_steps:
                    sd = (
                        dict(s)
                        if isinstance(s, dict)
                        else {
                            "step_id": str(s[0]),
                            "run_id": str(s[1]),
                            "step_number": s[2],
                            "action_type": s[3],
                            "tool_name": s[4],
                            "tool_input": s[5],
                            "tool_output": s[6],
                            "latency_ms": s[7],
                            "error_message": s[8],
                            "created_at": s[9].isoformat() if s[9] else None,
                        }
                    )
                    sd["step_id"] = str(sd["step_id"])
                    sd["run_id"] = str(sd["run_id"])
                    if isinstance(sd.get("tool_input"), (dict, list)):
                        sd["tool_input"] = json.dumps(sd["tool_input"], ensure_ascii=False)
                    if isinstance(sd.get("tool_output"), (dict, list)):
                        sd["tool_output"] = json.dumps(sd["tool_output"], ensure_ascii=False)
                    if isinstance(sd.get("created_at"), datetime):
                        sd["created_at"] = sd["created_at"].isoformat()
                    steps_data.append(sd)

                # 3. 轉為 Apache Parquet 檔案
                runs_parquet_path = year_dir / f"agent_runs_{timestamp_str}.parquet"
                runs_table = pa.Table.from_pylist(runs_data)
                pq.write_table(runs_table, runs_parquet_path, compression="snappy")

                steps_parquet_path = year_dir / f"agent_steps_{timestamp_str}.parquet"
                steps_table = pa.Table.from_pylist(steps_data)
                pq.write_table(steps_table, steps_parquet_path, compression="snappy")

                created_files = [runs_parquet_path, steps_parquet_path]

                # 4. 雙模冷儲存：嘗試上傳 GCS
                destination = "local"
                gcs_prefix = f"traces/year={now.year}/month={now.month:02d}"
                uploaded_runs = self._try_upload_to_gcs(runs_parquet_path, f"{gcs_prefix}/{runs_parquet_path.name}")
                uploaded_steps = self._try_upload_to_gcs(steps_parquet_path, f"{gcs_prefix}/{steps_parquet_path.name}")
                if uploaded_runs and uploaded_steps:
                    destination = "gcs"

                # 5. 熱層清理：CASCADE 刪除 Neon DB 已歸檔記錄
                if delete_after_archive:
                    await cur.execute("DELETE FROM agent_runs WHERE run_id = ANY(%s);", (expired_run_ids,))
                    await conn.commit()
                    logger.info(f"Cleaned up {len(expired_run_ids)} expired agent_runs from Neon DB.")

                return {
                    "status": "success",
                    "runs_archived": len(runs_data),
                    "steps_archived": len(steps_data),
                    "parquet_files": [str(p) for p in created_files],
                    "destination": destination,
                }

    def analyze_traces_duckdb(self, parquet_glob: Optional[str] = None) -> dict[str, Any]:
        """使用 DuckDB 引擎直接對 Parquet 冷資料進行極速 OLAP 分析 (0 資料庫負載)"""
        glob_path = parquet_glob or str(self.local_dir / "**" / "agent_runs_*.parquet")
        matching_files = list(self.local_dir.glob("**/agent_runs_*.parquet"))
        if not matching_files and not parquet_glob:
            return {"error": "No Parquet trace files found in cold storage."}

        con = duckdb.connect()
        try:
            # 1. 總量與成功率
            summary_query = f"""
                SELECT
                    COUNT(*) AS total_runs,
                    COUNT(CASE WHEN status = 'completed' THEN 1 END) AS successful_runs,
                    COUNT(CASE WHEN status = 'failed' THEN 1 END) AS failed_runs,
                    AVG(latency_ms) AS avg_latency_ms,
                    QUANTILE_CONT(latency_ms, 0.5) AS p50_latency_ms,
                    QUANTILE_CONT(latency_ms, 0.95) AS p95_latency_ms,
                    MAX(latency_ms) AS max_latency_ms,
                    SUM(total_tokens) AS total_tokens_used
                FROM '{glob_path}';
            """
            summary_row = con.execute(summary_query).fetchone()

            # 2. 各模型使用分佈與成本估算 (Groq $0.05/1M vs Gemini $0.075/1M)
            model_query = f"""
                SELECT
                    COALESCE(model_used, 'unknown') AS model,
                    COUNT(*) AS count,
                    SUM(total_tokens) AS tokens,
                    ROUND(AVG(latency_ms), 1) AS avg_latency
                FROM '{glob_path}'
                GROUP BY model_used
                ORDER BY count DESC;
            """
            model_rows = con.execute(model_query).fetchall()

            models_breakdown = [
                {
                    "model": r[0],
                    "calls": r[1],
                    "tokens": r[2] or 0,
                    "avg_latency_ms": float(r[3]) if r[3] else 0.0,
                }
                for r in model_rows
            ]

            return {
                "total_runs": summary_row[0] if summary_row else 0,
                "successful_runs": summary_row[1] if summary_row else 0,
                "failed_runs": summary_row[2] if summary_row else 0,
                "success_rate": (
                    f"{(summary_row[1] / summary_row[0] * 100):.1f}%"
                    if summary_row and summary_row[0] > 0
                    else "100.0%"
                ),
                "avg_latency_ms": round(float(summary_row[3]), 1) if summary_row and summary_row[3] else 0.0,
                "p50_latency_ms": round(float(summary_row[4]), 1) if summary_row and summary_row[4] else 0.0,
                "p95_latency_ms": round(float(summary_row[5]), 1) if summary_row and summary_row[5] else 0.0,
                "max_latency_ms": summary_row[6] if summary_row else 0,
                "total_tokens_used": summary_row[7] if summary_row else 0,
                "models_breakdown": models_breakdown,
            }
        finally:
            con.close()
