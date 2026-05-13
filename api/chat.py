"""Chat + conversation history endpoints."""
from __future__ import annotations
import uuid
from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException

from services import state
from services.gemini import ask_satori
from models import AskRequest, RenameRequest

router = APIRouter()


@router.post("/ask")
def ask(payload: AskRequest) -> Dict[str, Any]:
    if not payload.question or not payload.question.strip():
        raise HTTPException(status_code=400, detail="No question provided")

    conversation_id = payload.conversation_id or str(uuid.uuid4())
    settings = state.load_settings()
    lang = payload.lang or "en"

    result = ask_satori(payload.question.strip(), conversation_id, settings, lang=lang)

    # Save both messages AFTER the LLM call so the history reflects what the
    # user asked and what Satori replied (same semantics as the legacy app)
    state.add_message_to_conversation(conversation_id, "user", payload.question.strip())
    state.add_message_to_conversation(conversation_id, "bot",  result["answer"])

    result["conversation_id"] = conversation_id
    return result


@router.get("/history")
def get_all_history() -> List[Dict[str, Any]]:
    """Return conversation summaries (no full messages) for the sidebar."""
    history = state.load_history()
    out: List[Dict[str, Any]] = []
    for conv in history:
        msg_count = conv.get("_seq", len(conv.get("messages", [])))
        out.append({
            "id":            conv["id"],
            "title":         conv["title"],
            "created_at":    conv["created_at"],
            "updated_at":    conv["updated_at"],
            "message_count": msg_count,
        })
    return out


@router.get("/history/{conversation_id}")
def get_conversation_detail(conversation_id: str) -> Dict[str, Any]:
    conv = state.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.delete("/history/{conversation_id}")
def delete_conversation_route(conversation_id: str) -> Dict[str, Any]:
    state.delete_conversation(conversation_id)
    return {"status": "deleted"}


@router.post("/history/{conversation_id}/rename")
def rename_conversation_route(conversation_id: str, payload: RenameRequest) -> Dict[str, Any]:
    title = (payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title required")
    state.rename_conversation(conversation_id, title)
    return {"status": "renamed"}
