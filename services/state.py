"""
Satori — State store thin re-export
====================================
Wraps firestore_client.store with the settings post-load fix-ups
(merge defaults, pull secrets from Secret Manager, migrate old keys).
"""

from typing import Any, Dict
from secret_manager import get_secret, is_secret_key
from firestore_client import store as _store
from config import DEFAULT_SETTINGS


def load_settings() -> Dict[str, Any]:
    """Load settings via the configured state backend with post-load fix-ups."""
    try:
        saved = _store.load_settings() or {}
    except Exception:
        saved = {}

    # Auto-migrate: old single-table → multi-table
    if "bq_table" in saved and "bq_tables" not in saved:
        saved["bq_tables"] = [{
            "table_name": saved.pop("bq_table"),
            "schema_description": saved.pop("schema_description", "AUTO_DETECT"),
        }]
    merged = {**DEFAULT_SETTINGS, **saved}
    if "bq_tables" in saved:
        merged["bq_tables"] = saved["bq_tables"]
    # Resolve secrets from Secret Manager
    for key in ("gemini_api_key",):
        val = get_secret(key)
        if val:
            merged[key] = val
    # Drop deprecated matrix categories, add project_engagement if missing
    if "matrix_categories" in merged:
        cat_ids = {c["id"] for c in merged["matrix_categories"]}
        deprecated = {"availability", "experience"}
        if deprecated & cat_ids and "project_engagement" not in cat_ids:
            merged["matrix_categories"] = [c for c in merged["matrix_categories"] if c["id"] not in deprecated]
            merged["matrix_categories"].append({
                "id": "project_engagement",
                "name": "Project Engagement",
                "weight": 35,
                "reasoning": "Scores employees higher based on the number of active projects they are currently allocated to. More projects indicates greater involvement and contribution across the organisation. Normalised to 0-100 (capped at 5 projects = 100%). Derived from Allocation_data: distinct project count per employee.",
            })
            total = sum(c["weight"] for c in merged["matrix_categories"])
            if total != 100:
                for c in merged["matrix_categories"]:
                    c["weight"] = round(c["weight"] / total * 100)
    return merged


def save_settings(settings: Dict[str, Any]) -> None:
    """Persist settings — strip secrets first so they only live in Secret Manager."""
    clean = {k: v for k, v in settings.items() if not is_secret_key(k)}
    _store.save_settings(clean)


# Direct passthroughs
def load_history():                           return _store.load_history()
def save_history(history):                    return _store.save_history(history)
def get_conversation(cid):                    return _store.get_conversation(cid)
def get_conversation_messages(cid, limit=20): return _store.get_conversation_messages(cid, limit=limit)
def add_message_to_conversation(cid, role, content): return _store.add_message_to_conversation(cid, role, content)
def delete_conversation(cid):                 return _store.delete_conversation(cid)
def rename_conversation(cid, title):          return _store.rename_conversation(cid, title)
def store():                                  return _store
