"""Settings + BigQuery connection test endpoints."""
from __future__ import annotations
from typing import Any, Dict, List
from fastapi import APIRouter, Request

from secret_manager import is_secret_key, mask_secret, clear_cache as clear_secret_cache
from google.cloud import bigquery

from services import state
from services.gemini import reset_clients
from config import DEFAULT_SETTINGS

router = APIRouter()


def _safe(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Mask any secret-shaped values before returning to the client."""
    safe = dict(settings)
    for k in ("gemini_api_key",):
        if safe.get(k):
            safe[k] = mask_secret(safe[k])
    return safe


@router.get("/settings")
def get_settings_route() -> Dict[str, Any]:
    return _safe(state.load_settings())


@router.post("/settings")
async def update_settings_route(request: Request) -> Dict[str, Any]:
    data = await request.json()
    settings = state.load_settings()

    secret_updates, regular = {}, {}
    for k, v in data.items():
        if is_secret_key(k) and v:
            secret_updates[k] = v
        else:
            regular[k] = v

    settings.update(regular)
    state.save_settings(settings)
    if secret_updates:
        clear_secret_cache()
        settings.update(secret_updates)
    # Bust cached Gemini / BQ clients so the next request picks up the new config
    reset_clients()
    return {"status": "saved", "settings": _safe(settings)}


@router.post("/settings/reset")
def reset_settings_route() -> Dict[str, Any]:
    state.save_settings(DEFAULT_SETTINGS)
    clear_secret_cache()
    reset_clients()
    return {"status": "reset", "settings": _safe(state.load_settings())}


@router.post("/api/test-connection")
async def test_connection(request: Request) -> Dict[str, Any]:
    data = (await request.json()) or {}
    settings = state.load_settings()
    project = data.get("gcp_project", settings["gcp_project"])
    dataset = data.get("bq_dataset", settings["bq_dataset"])

    tables_to_test = data.get("bq_tables", [])
    if not tables_to_test:
        tables_to_test = [t["table_name"] for t in settings.get("bq_tables", [])]

    try:
        client = bigquery.Client(project=project)
        results: List[Dict[str, Any]] = []
        for tbl in tables_to_test:
            try:
                full = f"`{project}.{dataset}.{tbl}`"
                rows = list(client.query(f"SELECT COUNT(*) as total_rows FROM {full} LIMIT 1").result())
                results.append({"table": tbl, "status": "ok", "rows": rows[0].total_rows if rows else 0})
            except Exception as te:
                results.append({"table": tbl, "status": "error", "error": str(te)})
        ok = sum(1 for r in results if r["status"] == "ok")
        total = sum(r.get("rows", 0) for r in results if r["status"] == "ok")
        if ok == len(results):
            return {"status": "connected", "message": f"All {ok} table(s) connected. Total: {total:,} rows.", "tables": results}
        return {"status": "partial", "message": f"{ok}/{len(results)} table(s) connected.", "tables": results}
    except Exception as e:
        err = str(e)
        hint = err
        if "Could not automatically determine credentials" in err:
            hint = "Google Cloud credentials not found. Run 'gcloud auth application-default login'."
        elif "Not found" in err:
            hint = f"Dataset not found. Verify '{dataset}' exists in BigQuery."
        elif "Permission" in err or "403" in err:
            hint = "Permission denied. Make sure your account has BigQuery access on this project."
        return {"status": "error", "message": hint, "error": err}
