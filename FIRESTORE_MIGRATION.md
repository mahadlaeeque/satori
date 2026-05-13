# Satori — Operational Data Migration (JSON → Firestore)

This document is the runbook for moving Satori's user state off the local
filesystem and into Firestore. It closes the **Operational Data** gap from
the platform standards audit.

## What changed in the code

| File | What it does |
|---|---|
| `firestore_client.py` | New. `StateStore` class with Firestore + JSON backends, selected by `SATORI_STATE_BACKEND` env var. |
| `app.py` | `load_settings`, `save_settings`, `load_history`, `save_history`, `get_conversation`, `get_conversation_messages`, `add_message_to_conversation`, `delete_conversation`, `rename_conversation` now delegate to the state store. |
| `scripts/migrate_state.py` | New. One-shot migrator that reads the two JSON files and writes them into Firestore using the same schema. |
| `requirements.txt` | Added `google-cloud-firestore>=2.16.0` (and `gunicorn` for the upcoming Cloud Run move). |

**Default backend is still JSON** — nothing changes for current users until you flip the env var.

## Firestore schema

```
settings/global                       (single doc, full settings blob)
conversations/{conversation_id}       (doc with title, created_at, updated_at, _seq)
  └── messages/{auto_id}              (subcollection: role, content, timestamp, seq)
```

`_seq` on each conversation doc is a monotonic counter incremented on every
new message — used as the message count in the sidebar and as the sort key for
the `messages` subcollection.

## What you need to do on GCP

There are four steps. Total time ~15 minutes once you have the right
permissions.

### 1. Provision Firestore

Pick a region close to your other workloads — same region as your BigQuery
dataset is usually right. For TMC's `ai-vertex-mahad` project I'd suggest
`asia-south1` (Mumbai) or whichever region your BQ dataset already lives in.

```bash
gcloud config set project ai-vertex-mahad

# Create the database. --location is the regional one (not multi-region) so
# we stay close to the BQ workloads.
gcloud firestore databases create \
    --location=asia-south1 \
    --type=firestore-native
```

If you prefer the console:
**Firestore → Create database → Native mode → asia-south1**.

### 2. Grant the runtime service account access

Until we have a Cloud Run service account, use Application Default
Credentials (your own user, via `gcloud auth application-default login`)
for the migration step. For the running app, when it eventually runs on
Cloud Run, the service account needs:

```bash
SVC_ACCOUNT="satori-runtime@ai-vertex-mahad.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding ai-vertex-mahad \
    --member="serviceAccount:${SVC_ACCOUNT}" \
    --role="roles/datastore.user"
```

If you haven't created `satori-runtime` yet, do that first:

```bash
gcloud iam service-accounts create satori-runtime \
    --display-name="Satori Cloud Run runtime"
```

### 3. Run the migration

From the `satori-chatbot/` directory:

```bash
# 1) Ensure you can talk to GCP
gcloud auth application-default login

# 2) Dry-run first to see what would be written
python scripts/migrate_state.py --dry-run

# 3) Real run
python scripts/migrate_state.py
```

Expected output:

```
[settings] OK (12 keys)
[conversations] Wrote 43 conversations, 142 messages.
[verify] Reading back counts …
   settings/global exists: True
   conversations count:   43
Done. Next step: set SATORI_STATE_BACKEND=firestore and restart the app.
```

**Re-running safely:** the migrator is idempotent for settings and conversation
metadata, but messages are *appended* on each run. If you need to re-run the
full migration after edits, use `--wipe` (clears Firestore first) or use
`--skip-messages` if you only changed metadata.

### 4. Flip the app to Firestore mode

Two ways. Local dev:

```bash
export SATORI_STATE_BACKEND=firestore
python app.py
```

You should see this in the startup banner:

```
   State:    firestore
```

For Cloud Run (when we land Hosting): set the env var on the service:

```bash
gcloud run services update satori-web \
    --update-env-vars SATORI_STATE_BACKEND=firestore,SATORI_GCP_PROJECT=ai-vertex-mahad
```

## Sanity checks after the cutover

After flipping to Firestore mode, walk through these in the UI:

1. Open the app → sidebar should still show all old conversations.
2. Click a conversation → all messages still there in the right order.
3. Start a new chat → message persists across a page refresh.
4. Rename a conversation → title updates and sticks after refresh.
5. Delete a conversation → it disappears and doesn't come back.

Then run this verification one-liner from a fresh terminal:

```bash
python -c "
from google.cloud import firestore
c = firestore.Client(project='ai-vertex-mahad')
print('settings/global:', c.collection('settings').document('global').get().exists)
print('conversations  :', sum(1 for _ in c.collection('conversations').list_documents()))
"
```

## Rollback

If something looks wrong, flip back immediately:

```bash
unset SATORI_STATE_BACKEND       # or: export SATORI_STATE_BACKEND=json
```

The original `chat_history.json` and `settings.json` files are untouched by
the migration, so JSON mode resumes exactly where it left off. Leave them in
place for **one full release cycle** before deleting them, so we have a fast
escape hatch.

## What still needs to happen (not in this change)

These were called out in the game plan but are deliberately out of scope here
so we can ship this change in isolation:

- **Cloud SQL Postgres** for the upcoming `projects`, `task_assignments`, and
  `audit_log` tables (Operational Data, second half). Not needed yet because
  no relational-shaped data exists.
- **In-memory cache** on `load_settings` (60s TTL) to avoid hot-pathing
  Firestore on every request. Worth adding once we see real read volume on
  Cloud Run.
- **Schema validation** on conversations — today the message shape is informal.
  Recommend adding `pydantic` models in a follow-up PR.

## Troubleshooting

- **`Firestore backend init failed: 403 Permission denied`** — the principal
  running the app/script doesn't have `roles/datastore.user`. See step 2.
- **`The user does not have permission to access project`** — `gcloud auth
  application-default login` needs to be run as a user that's in the project.
- **App started but `State: json` in the banner** — `SATORI_STATE_BACKEND`
  isn't reaching the process. Check `printenv SATORI_STATE_BACKEND` in the
  same shell.
- **Migration script fails with `cannot import name 'firestore'`** — install
  the dependency: `pip install google-cloud-firestore>=2.16.0`.
- **Messages appear duplicated after re-running migration** — re-run with
  `--wipe` then the standard command.
