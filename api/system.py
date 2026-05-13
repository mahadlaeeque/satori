"""System endpoints: health, tables, schema, drive sync, debug."""
from __future__ import annotations
import traceback
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException

from services import state
from services.gemini import build_full_table, get_bq_client

router = APIRouter()


@router.get("/api/health")
def health() -> Dict[str, Any]:
    s = state.load_settings()
    return {"status": "ok", "project": s["gcp_project"], "table": build_full_table(s)}


@router.get("/api/tables")
def get_tables() -> Dict[str, Any]:
    s = state.load_settings()
    return {"tables": s.get("bq_tables", [])}


@router.get("/api/table-schema/{table_name}")
def detect_table_schema(table_name: str) -> Dict[str, Any]:
    s = state.load_settings()
    project = s["gcp_project"]
    dataset = s["bq_dataset"]
    try:
        client = get_bq_client(s)
        sql = f"""
        SELECT column_name, data_type
        FROM `{project}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = '{table_name}'
        ORDER BY ordinal_position
        """
        cols = [{"name": r.column_name, "type": r.data_type} for r in client.query(sql).result()]
        if not cols:
            raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found in dataset '{dataset}'.")
        return {"table": table_name, "columns": cols}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/sync-drive")
def sync_drive_data(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = payload or {}
    try:
        import drive_sync
        local = bool(payload.get("local", False))
        dry_run = bool(payload.get("dry_run", False))
        result = drive_sync.sync(local=local, dry_run=dry_run) if hasattr(drive_sync, "sync") else {"status": "no_sync_fn"}
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e), "trace": traceback.format_exc()}
