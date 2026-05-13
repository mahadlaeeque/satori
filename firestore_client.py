"""
Satori — State Store (Firestore + JSON fallback)
=================================================
Replaces direct JSON-file persistence with a backend-agnostic StateStore.

Modes:
  • SATORI_STATE_BACKEND=firestore   → Firestore (production)
  • SATORI_STATE_BACKEND=json (default) → local JSON files (dev / offline)
  • SATORI_STATE_BACKEND=auto        → try Firestore, fall back to JSON if init fails

Firestore schema:
  settings/global                    — single doc, full settings blob
  conversations/{conversation_id}    — doc per conversation (id, title, created_at, updated_at)
    └─ messages/{auto_id}            — subcollection: { role, content, timestamp, seq }

Public API (mirrors the old module-level functions in app.py):
  load_settings()                                       → dict
  save_settings(settings: dict)                         → None
  load_history()                                        → list[dict]  (summary, no full messages)
  save_history(history: list[dict])                     → None        (full replace; used by /delete + /rename)
  get_conversation(conversation_id: str)                → dict | None  (with messages)
  get_conversation_messages(conv_id, limit=20)          → list[dict]
  add_message_to_conversation(conv_id, role, content)   → dict (the conversation)
  delete_conversation(conversation_id: str)             → None
  rename_conversation(conversation_id: str, title: str) → None

Usage:
  from firestore_client import store
  settings = store.load_settings()

The Flask app uses thin module-level wrappers (in app.py) that delegate to the
singleton `store`, so existing call sites keep working unchanged.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

log = logging.getLogger("satori.state")

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# File paths (used in JSON mode AND as the migration source)
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
HISTORY_FILE  = os.path.join(BASE_DIR, "chat_history.json")

# Backend selection
BACKEND = os.environ.get("SATORI_STATE_BACKEND", "json").lower()  # json | firestore | auto

# Firestore config
FIRESTORE_PROJECT  = os.environ.get("SATORI_GCP_PROJECT", "ai-vertex-mahad")
FIRESTORE_DATABASE = os.environ.get("SATORI_FIRESTORE_DB", "(default)")

SETTINGS_DOC_PATH         = ("settings", "global")
CONVERSATIONS_COLLECTION  = "conversations"
MESSAGES_SUBCOLLECTION    = "messages"


# --------------------------------------------------------------------------
# Backend: JSON
# --------------------------------------------------------------------------
class _JsonBackend:
    """File-based backend — preserves the legacy behaviour exactly."""

    name = "json"

    def load_settings(self) -> Dict[str, Any]:
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r") as f:
                    return json.load(f)
            except Exception as e:
                log.warning("Failed to read %s: %s", SETTINGS_FILE, e)
        return {}

    def save_settings(self, settings: Dict[str, Any]) -> None:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)

    def load_history(self) -> List[Dict[str, Any]]:
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    return json.load(f)
            except Exception as e:
                log.warning("Failed to read %s: %s", HISTORY_FILE, e)
        return []

    def save_history(self, history: List[Dict[str, Any]]) -> None:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)


# --------------------------------------------------------------------------
# Backend: Firestore
# --------------------------------------------------------------------------
class _FirestoreBackend:
    """
    Firestore Native-mode backend.

    Conversations live as docs at conversations/{id}; their messages live in
    a subcollection messages/{auto_id} ordered by a monotonic `seq` field so
    we can preserve insertion order even when timestamps collide.
    """

    name = "firestore"

    def __init__(self):
        from google.cloud import firestore  # imported lazily so JSON-mode users don't need it
        self._fs = firestore
        try:
            self._client = firestore.Client(project=FIRESTORE_PROJECT, database=FIRESTORE_DATABASE)
        except TypeError:
            # Older google-cloud-firestore versions don't accept `database=`
            self._client = firestore.Client(project=FIRESTORE_PROJECT)
        log.info("Firestore client ready: project=%s database=%s", FIRESTORE_PROJECT, FIRESTORE_DATABASE)

    # ── Settings ───────────────────────────────────────────────────────────
    def load_settings(self) -> Dict[str, Any]:
        snap = self._client.collection(SETTINGS_DOC_PATH[0]).document(SETTINGS_DOC_PATH[1]).get()
        if snap.exists:
            return snap.to_dict() or {}
        return {}

    def save_settings(self, settings: Dict[str, Any]) -> None:
        self._client.collection(SETTINGS_DOC_PATH[0]).document(SETTINGS_DOC_PATH[1]).set(settings)

    # ── History (summary list) ─────────────────────────────────────────────
    def load_history(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        # Order by updated_at descending so newest convos first (matches the UI)
        q = (self._client.collection(CONVERSATIONS_COLLECTION)
                         .order_by("updated_at", direction=self._fs.Query.DESCENDING))
        for doc in q.stream():
            d = doc.to_dict() or {}
            d["id"] = doc.id
            # Caller expects `messages` to be present (even if empty) — fetch lazily
            d.setdefault("messages", [])
            out.append(d)
        return out

    def save_history(self, history: List[Dict[str, Any]]) -> None:
        """
        Full-replace semantics for the legacy callers (used by delete + rename in
        app.py). We diff against what's currently in Firestore and apply minimal
        deletes/writes rather than blowing the whole collection away.
        """
        existing_ids = {doc.id for doc in self._client.collection(CONVERSATIONS_COLLECTION).list_documents()}
        new_ids = {c["id"] for c in history if c.get("id")}

        batch = self._client.batch()
        n_ops = 0

        # Deletes: anything that used to exist but isn't in the new list
        for gone in existing_ids - new_ids:
            batch.delete(self._client.collection(CONVERSATIONS_COLLECTION).document(gone))
            n_ops += 1
            if n_ops >= 450:
                batch.commit(); batch = self._client.batch(); n_ops = 0

        # Upserts: copy metadata fields only — never overwrite the messages subcollection
        for conv in history:
            cid = conv.get("id")
            if not cid:
                continue
            doc_ref = self._client.collection(CONVERSATIONS_COLLECTION).document(cid)
            meta = {k: v for k, v in conv.items() if k not in ("id", "messages")}
            batch.set(doc_ref, meta, merge=True)
            n_ops += 1
            if n_ops >= 450:
                batch.commit(); batch = self._client.batch(); n_ops = 0

        if n_ops:
            batch.commit()

    # ── Conversation detail ────────────────────────────────────────────────
    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        ref = self._client.collection(CONVERSATIONS_COLLECTION).document(conversation_id)
        snap = ref.get()
        if not snap.exists:
            return None
        conv = snap.to_dict() or {}
        conv["id"] = conversation_id
        conv["messages"] = self._fetch_messages(conversation_id)
        return conv

    def get_conversation_messages(self, conversation_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        # Get the most recent `limit` messages but return them oldest-first
        msgs_ref = (self._client.collection(CONVERSATIONS_COLLECTION)
                                .document(conversation_id)
                                .collection(MESSAGES_SUBCOLLECTION)
                                .order_by("seq", direction=self._fs.Query.DESCENDING)
                                .limit(limit))
        msgs = [d.to_dict() for d in msgs_ref.stream()]
        msgs.reverse()
        return msgs

    def add_message_to_conversation(self, conversation_id: str, role: str, content: str) -> Dict[str, Any]:
        ref = self._client.collection(CONVERSATIONS_COLLECTION).document(conversation_id)
        now = datetime.now().isoformat()

        # Create the conversation doc on first message
        snap = ref.get()
        if not snap.exists:
            title = content[:50] + ("..." if len(content) > 50 else "")
            ref.set({
                "title":      title,
                "created_at": now,
                "updated_at": now,
                "_seq":       0,
            })
            conv_data = {"title": title, "created_at": now, "updated_at": now, "_seq": 0}
        else:
            conv_data = snap.to_dict() or {}

        # Append message with a monotonic seq so we can sort reliably
        next_seq = int(conv_data.get("_seq", 0)) + 1
        ref.collection(MESSAGES_SUBCOLLECTION).add({
            "role":      role,
            "content":   content,
            "timestamp": now,
            "seq":       next_seq,
        })
        ref.update({"_seq": next_seq, "updated_at": now})

        # Return the same shape the JSON version did, so callers don't notice the swap
        full = ref.get().to_dict() or {}
        full["id"] = conversation_id
        full["messages"] = self._fetch_messages(conversation_id)
        return full

    # ── Helpers ────────────────────────────────────────────────────────────
    def _fetch_messages(self, conversation_id: str) -> List[Dict[str, Any]]:
        msgs_ref = (self._client.collection(CONVERSATIONS_COLLECTION)
                                .document(conversation_id)
                                .collection(MESSAGES_SUBCOLLECTION)
                                .order_by("seq"))
        return [d.to_dict() for d in msgs_ref.stream()]


# --------------------------------------------------------------------------
# StateStore — the public façade
# --------------------------------------------------------------------------
class StateStore:
    """
    Backend-agnostic state store. Decides Firestore vs. JSON at construction
    time. Falls back to JSON if Firestore init fails (logged once).
    """

    def __init__(self, backend: Optional[str] = None):
        backend = (backend or BACKEND).lower()
        self._json = _JsonBackend()
        self._fs: Optional[_FirestoreBackend] = None

        if backend == "firestore":
            self._fs = self._init_firestore(required=True)
        elif backend == "auto":
            self._fs = self._init_firestore(required=False)

        self.mode = "firestore" if self._fs is not None else "json"
        log.info("StateStore backend = %s", self.mode)

    def _init_firestore(self, required: bool) -> Optional[_FirestoreBackend]:
        try:
            return _FirestoreBackend()
        except Exception as e:
            msg = f"Firestore backend init failed: {e}"
            if required:
                log.error(msg)
                raise
            log.warning("%s — falling back to JSON.", msg)
            return None

    # ── Settings ───────────────────────────────────────────────────────────
    def load_settings(self) -> Dict[str, Any]:
        if self._fs:
            data = self._fs.load_settings()
            if data:
                return data
            # First boot on Firestore: fall through to JSON so we don't lose anything
            return self._json.load_settings()
        return self._json.load_settings()

    def save_settings(self, settings: Dict[str, Any]) -> None:
        if self._fs:
            self._fs.save_settings(settings)
        # Also write to JSON when on Firestore? No — single source of truth.
        # In JSON mode, persist to file:
        else:
            self._json.save_settings(settings)

    # ── History ────────────────────────────────────────────────────────────
    def load_history(self) -> List[Dict[str, Any]]:
        if self._fs:
            return self._fs.load_history()
        return self._json.load_history()

    def save_history(self, history: List[Dict[str, Any]]) -> None:
        if self._fs:
            self._fs.save_history(history)
        else:
            self._json.save_history(history)

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        if self._fs:
            return self._fs.get_conversation(conversation_id)
        # JSON mode: replicate the legacy linear scan
        for conv in self._json.load_history():
            if conv.get("id") == conversation_id:
                return conv
        return None

    def get_conversation_messages(self, conversation_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        if self._fs:
            return self._fs.get_conversation_messages(conversation_id, limit=limit)
        conv = self.get_conversation(conversation_id)
        if conv is None:
            return []
        return conv.get("messages", [])[-limit:]

    def add_message_to_conversation(self, conversation_id: str, role: str, content: str) -> Dict[str, Any]:
        if self._fs:
            return self._fs.add_message_to_conversation(conversation_id, role, content)

        # JSON mode: legacy in-memory update + full re-save
        history = self._json.load_history()
        conv = next((c for c in history if c.get("id") == conversation_id), None)
        now = datetime.now().isoformat()
        if conv is None:
            title = content[:50] + ("..." if len(content) > 50 else "")
            conv = {
                "id": conversation_id,
                "title": title,
                "created_at": now,
                "updated_at": now,
                "messages": [],
            }
            history.insert(0, conv)
        conv["messages"].append({"role": role, "content": content, "timestamp": now})
        conv["updated_at"] = now
        self._json.save_history(history)
        return conv

    def delete_conversation(self, conversation_id: str) -> None:
        if self._fs:
            # Best-effort recursive delete: clear messages subcollection, then the doc
            doc_ref = self._fs._client.collection(CONVERSATIONS_COLLECTION).document(conversation_id)
            for m in doc_ref.collection(MESSAGES_SUBCOLLECTION).list_documents():
                m.delete()
            doc_ref.delete()
            return
        history = [c for c in self._json.load_history() if c.get("id") != conversation_id]
        self._json.save_history(history)

    def rename_conversation(self, conversation_id: str, new_title: str) -> None:
        if self._fs:
            self._fs._client.collection(CONVERSATIONS_COLLECTION).document(conversation_id).update({
                "title": new_title,
                "updated_at": datetime.now().isoformat(),
            })
            return
        history = self._json.load_history()
        for conv in history:
            if conv.get("id") == conversation_id:
                conv["title"] = new_title
                conv["updated_at"] = datetime.now().isoformat()
                break
        self._json.save_history(history)


# --------------------------------------------------------------------------
# Module-level singleton — app.py imports this
# --------------------------------------------------------------------------
store = StateStore()


def get_store() -> StateStore:
    """Accessor (useful for tests that need to swap the backend)."""
    return store
