"""
Satori — Pydantic request/response models
==========================================
Shapes are deliberately permissive (most fields optional) so we keep
wire-format compatibility with the legacy Flask routes during the FastAPI
migration. The existing frontend will continue to talk to these endpoints
unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ── Chat ──────────────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str
    conversation_id: Optional[str] = None
    lang: Optional[str] = "en"


class AskResponse(BaseModel):
    answer: str
    conversation_id: str
    sources: Optional[List[Any]] = None
    sql: Optional[str] = None


class RenameRequest(BaseModel):
    title: str


# ── Settings ──────────────────────────────────────────────────────────────
class SettingsPayload(BaseModel):
    """Free-form settings payload — keys vary by deployment."""
    model_config = {"extra": "allow"}


# ── Matrix categories ─────────────────────────────────────────────────────
class MatrixCategoriesPayload(BaseModel):
    categories: List[Dict[str, Any]] = Field(default_factory=list)


# ── Connection test ───────────────────────────────────────────────────────
class TestConnectionRequest(BaseModel):
    gcp_project: Optional[str] = None
    bq_dataset: Optional[str] = None
    bq_tables: Optional[List[str]] = None


# ── AI staff suggestions ──────────────────────────────────────────────────
class StaffSuggestionRequest(BaseModel):
    project_name: str
    project_description: Optional[str] = ""
    department: str
    skills_required: Optional[str] = ""


# ── AI productivity tip ───────────────────────────────────────────────────
class ProductivityTipRequest(BaseModel):
    name: str
    pattern: Optional[Dict[str, Any]] = None


# ── TTS ───────────────────────────────────────────────────────────────────
class TtsRequest(BaseModel):
    text: str
    voice: Optional[str] = None


class TtsPreviewRequest(BaseModel):
    voice: str
    text: Optional[str] = None


# ── Transliteration ───────────────────────────────────────────────────────
class TransliterateRequest(BaseModel):
    text: str


# ── Help ──────────────────────────────────────────────────────────────────
class HelpRequest(BaseModel):
    question: str


# ── Sync drive ────────────────────────────────────────────────────────────
class SyncDriveRequest(BaseModel):
    local: Optional[bool] = False
    dry_run: Optional[bool] = False
