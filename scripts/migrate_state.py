#!/usr/bin/env python3
"""
Satori — One-shot state migration: JSON files → Firestore
==========================================================

Reads the legacy chat_history.json and settings.json from disk and writes
their contents into Firestore using the same schema firestore_client.py
expects:

    settings/global                  ← settings.json
    conversations/{conversation_id}  ← each conversation (metadata)
      └─ messages/{auto_id}          ← every message, ordered by `seq`

Usage:
    # Default project (ai-vertex-mahad), default database ('(default)')
    python scripts/migrate_state.py

    # Dry run — show what would be written
    python scripts/migrate_state.py --dry-run

    # Custom project / database
    python scripts/migrate_state.py --project my-project --database my-db

    # Wipe existing Firestore data first (DANGEROUS)
    python scripts/migrate_state.py --wipe

Idempotent: re-running overwrites the settings doc and updates conversation
metadata, but appends NEW messages each time. Use --skip-messages when
re-running after the first migration to avoid duplicates, or use --wipe.

Exit codes:
    0  success
    1  source files missing
    2  Firestore init failed
    3  migration error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

# Make the parent directory importable so we can reuse constants
HERE   = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
sys.path.insert(0, PARENT)

DEFAULT_SETTINGS_FILE = os.path.join(PARENT, "settings.json")
DEFAULT_HISTORY_FILE  = os.path.join(PARENT, "chat_history.json")


def read_json(path: str, fallback):
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"  ! Failed to read {path}: {e}", file=sys.stderr)
        return fallback


def migrate(args) -> int:
    print("=" * 60)
    print("  Satori state migration — JSON → Firestore")
    print("=" * 60)
    print(f"  Project:        {args.project}")
    print(f"  Database:       {args.database}")
    print(f"  Settings src:   {args.settings_file}")
    print(f"  History src:    {args.history_file}")
    print(f"  Dry run:        {args.dry_run}")
    print(f"  Wipe first:     {args.wipe}")
    print(f"  Skip messages:  {args.skip_messages}")
    print("=" * 60)

    settings = read_json(args.settings_file, {})
    history  = read_json(args.history_file, [])

    if not settings and not history:
        print("Nothing to migrate (both source files empty/missing).", file=sys.stderr)
        return 1

    print(f"  → Settings keys: {len(settings)}")
    print(f"  → Conversations: {len(history)}")
    total_msgs = sum(len(c.get('messages', [])) for c in history)
    print(f"  → Total messages: {total_msgs}")

    if args.dry_run:
        print("\nDry run — no writes.")
        for c in history[:3]:
            print(f"   • {c.get('id','?')[:8]}  {c.get('title','(no title)')[:60]}  "
                  f"({len(c.get('messages', []))} msgs)")
        if len(history) > 3:
            print(f"   … and {len(history) - 3} more")
        return 0

    # --- Initialise Firestore --------------------------------------------
    try:
        from google.cloud import firestore
        try:
            client = firestore.Client(project=args.project, database=args.database)
        except TypeError:
            client = firestore.Client(project=args.project)
    except Exception as e:
        print(f"Firestore init failed: {e}", file=sys.stderr)
        return 2

    # --- Optional wipe ---------------------------------------------------
    if args.wipe:
        print("\n[wipe] Deleting existing conversations and settings/global …")
        for conv_doc in client.collection("conversations").list_documents():
            for msg in conv_doc.collection("messages").list_documents():
                msg.delete()
            conv_doc.delete()
        client.collection("settings").document("global").delete()
        print("[wipe] Done.")

    # --- Write settings --------------------------------------------------
    # Strip secrets before writing (they live in Secret Manager)
    SECRETS = {"gemini_api_key"}
    clean_settings = {k: v for k, v in (settings or {}).items() if k not in SECRETS}
    if clean_settings:
        print("\n[settings] Writing settings/global …")
        client.collection("settings").document("global").set(clean_settings)
        print(f"[settings] OK ({len(clean_settings)} keys)")
    else:
        print("\n[settings] (no settings to write)")

    # --- Write conversations + messages ----------------------------------
    print(f"\n[conversations] Writing {len(history)} conversations …")
    n_convs = 0
    n_msgs  = 0
    for conv in history:
        cid = conv.get("id")
        if not cid:
            continue
        ref = client.collection("conversations").document(cid)
        messages = conv.get("messages", []) or []
        meta = {
            "title":      conv.get("title") or "Untitled",
            "created_at": conv.get("created_at") or datetime.now().isoformat(),
            "updated_at": conv.get("updated_at") or datetime.now().isoformat(),
            "_seq":       len(messages),
        }
        ref.set(meta, merge=True)
        n_convs += 1

        if args.skip_messages:
            continue

        # Write messages in batches of 400 (Firestore batch limit is 500)
        batch = client.batch()
        ops = 0
        for i, msg in enumerate(messages, start=1):
            msg_ref = ref.collection("messages").document()
            batch.set(msg_ref, {
                "role":      msg.get("role", "user"),
                "content":   msg.get("content", ""),
                "timestamp": msg.get("timestamp") or datetime.now().isoformat(),
                "seq":       i,
            })
            ops += 1
            n_msgs += 1
            if ops >= 400:
                batch.commit()
                batch = client.batch()
                ops = 0
        if ops:
            batch.commit()
        print(f"   • {cid[:8]}  {meta['title'][:48]:<48}  {len(messages)} msgs")

    print(f"\n[conversations] Wrote {n_convs} conversations, {n_msgs} messages.")

    # --- Verification ----------------------------------------------------
    print("\n[verify] Reading back counts …")
    settings_back = client.collection("settings").document("global").get()
    print(f"   settings/global exists: {settings_back.exists}")
    conv_count = sum(1 for _ in client.collection("conversations").list_documents())
    print(f"   conversations count:   {conv_count}")
    if conv_count != n_convs and not args.skip_messages:
        print("   ⚠ Mismatch — please re-check.", file=sys.stderr)

    print("\nDone. Next step: set SATORI_STATE_BACKEND=firestore and restart the app.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project",       default=os.environ.get("SATORI_GCP_PROJECT", "ai-vertex-mahad"))
    ap.add_argument("--database",      default=os.environ.get("SATORI_FIRESTORE_DB", "(default)"))
    ap.add_argument("--settings-file", default=DEFAULT_SETTINGS_FILE)
    ap.add_argument("--history-file",  default=DEFAULT_HISTORY_FILE)
    ap.add_argument("--dry-run",       action="store_true", help="Show what would be written; do not touch Firestore")
    ap.add_argument("--wipe",          action="store_true", help="Delete existing settings/global and all conversations first")
    ap.add_argument("--skip-messages", action="store_true", help="Only write conversation metadata (re-run safety)")
    args = ap.parse_args()

    try:
        return migrate(args)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\nMigration failed: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
