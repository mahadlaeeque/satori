# Satori — Cloud Run Hosting Runbook

This runbook bootstraps the **satori-web** Cloud Run service and the IAM
plumbing it needs. Run it once per GCP project. After that, `cloudbuild.yaml`
deploys every commit to `main` automatically.

## What you're creating

| Resource | Purpose |
| --- | --- |
| Cloud Run service `satori-web` | The actual hosting target. Auto-scales 0→10. |
| Service account `satori-runtime` | Identity the container runs as. Holds the data-plane permissions. |
| IAM bindings on `satori-runtime` | BigQuery read, Firestore read/write, Secret Manager access. |
| IAM bindings on Cloud Build SA | `run.admin` + `iam.serviceAccountUser` so the pipeline can deploy on your behalf. |

## Prerequisites

- `gcloud` CLI authenticated as a Project Owner / Editor.
- The Artifact Registry repo `satori` in `us-central1` already exists (it does — that's where the image tags land).
- The Cloud Build trigger `satori-main-build` already exists (it does — `cloudbuild.yaml` is wired up).
- The Secret Manager secret `satori-gemini-api-key` already exists and has the live key as its current version.

## One-time bootstrap

Run these from your laptop, top to bottom. They are idempotent — re-running is safe.

### 1. Project / region defaults

```bash
export PROJECT_ID="ai-vertex-mahad"
export REGION="us-central1"
export SERVICE="satori-web"
export RUNTIME_SA="satori-runtime"
export RUNTIME_SA_EMAIL="${RUNTIME_SA}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "${PROJECT_ID}"
gcloud config set run/region "${REGION}"
```

### 2. Enable required APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  firestore.googleapis.com \
  bigquery.googleapis.com \
  cloudtrace.googleapis.com \
  iamcredentials.googleapis.com
```

### 3. Create the runtime service account

```bash
gcloud iam service-accounts create "${RUNTIME_SA}" \
  --display-name="Satori runtime (Cloud Run)" \
  --description="Identity that the satori-web container runs as. Read-only on BigQuery + Firestore data."
```

### 4. Grant the runtime SA its data-plane roles

```bash
# BigQuery — read all datasets in this project. If you'd rather scope tighter,
# grant roles/bigquery.dataViewer at the dataset level instead.
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA_EMAIL}" \
  --role="roles/bigquery.dataViewer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA_EMAIL}" \
  --role="roles/bigquery.jobUser"

# Firestore — Satori reads + writes conversations + settings.
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA_EMAIL}" \
  --role="roles/datastore.user"

# Secret Manager — pulls satori-gemini-api-key at startup.
gcloud secrets add-iam-policy-binding "satori-gemini-api-key" \
  --member="serviceAccount:${RUNTIME_SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"

# Cloud Trace — emit spans (only needed once OpenTelemetry is wired in).
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA_EMAIL}" \
  --role="roles/cloudtrace.agent"
```

### 5. Grant the Cloud Build SA the deploy roles

Cloud Build needs to update the Cloud Run service and act-as the runtime SA.

```bash
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
CLOUDBUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

# Update + deploy Cloud Run services.
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/run.admin"

# Required to pass the runtime SA to the new revision.
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA_EMAIL}" \
  --member="serviceAccount:${CLOUDBUILD_SA}" \
  --role="roles/iam.serviceAccountUser"
```

### 6. Create the Cloud Run service (first deploy)

Either run the build pipeline (which will create-or-update the service):

```bash
gcloud builds submit --config=cloudbuild.yaml \
  --substitutions=_TAG=$(git rev-parse --short HEAD)
```

…or pin the first revision by hand, using the most recent image:

```bash
gcloud run deploy "${SERVICE}" \
  --image="${REGION}-docker.pkg.dev/${PROJECT_ID}/satori/${SERVICE}:latest" \
  --region="${REGION}" \
  --platform=managed \
  --service-account="${RUNTIME_SA_EMAIL}" \
  --set-env-vars="SATORI_STATE_BACKEND=firestore,SATORI_GCP_PROJECT=${PROJECT_ID}" \
  --min-instances=0 \
  --max-instances=10 \
  --concurrency=80 \
  --cpu=1 \
  --memory=1Gi \
  --timeout=60s \
  --no-allow-unauthenticated
```

### 7. Verify

```bash
# Service URL.
URL=$(gcloud run services describe "${SERVICE}" --region="${REGION}" --format='value(status.url)')
echo "Satori is live at: ${URL}"

# Hit the health endpoint (needs an auth token since --no-allow-unauthenticated).
TOKEN=$(gcloud auth print-identity-token)
curl -H "Authorization: Bearer ${TOKEN}" "${URL}/api/health"
```

You should see `{"ok": true, ...}`.

## Granting end-user access

The service is private by default. To let a teammate hit it:

```bash
gcloud run services add-iam-policy-binding "${SERVICE}" \
  --region="${REGION}" \
  --member="user:teammate@tmcltd.ai" \
  --role="roles/run.invoker"
```

For an entire Google Workspace domain:

```bash
gcloud run services add-iam-policy-binding "${SERVICE}" \
  --region="${REGION}" \
  --member="domain:tmcltd.ai" \
  --role="roles/run.invoker"
```

## Configuration knobs

All env vars are set via `cloudbuild.yaml` step 4 (`--set-env-vars`). Add new ones there:

| Variable | Default | What it does |
| --- | --- | --- |
| `SATORI_STATE_BACKEND` | `firestore` | Selects the state store implementation in `firestore_client.py`. |
| `SATORI_GCP_PROJECT` | `$PROJECT_ID` | Which GCP project to talk to (BigQuery, Firestore, Secrets). |
| `PORT` | `8080` | Set by Cloud Run automatically — don't override. |
| `K_SERVICE` | (Cloud Run sets) | Used by `main.py` to decide whether to enable OpenTelemetry tracing. |

## Day-2 operations

### Tail logs

```bash
gcloud run services logs read "${SERVICE}" --region="${REGION}" --limit=100
```

…or open the Cloud Run service in the console, where logs and metrics are one click away.

### Roll back

Every revision is retained. To revert to a known-good revision:

```bash
gcloud run services update-traffic "${SERVICE}" \
  --region="${REGION}" \
  --to-revisions=<REVISION_NAME>=100
```

List recent revisions with `gcloud run revisions list --service=${SERVICE} --region=${REGION}`.

### Bump capacity

If chat or voice latency spikes under load, raise `--max-instances` and `--concurrency` in the `cloudbuild.yaml` deploy step, or do it inline:

```bash
gcloud run services update "${SERVICE}" --region="${REGION}" \
  --max-instances=25 --concurrency=80
```

## Troubleshooting

**Build green, deploy step fails with "permission denied".**
The Cloud Build SA is missing `roles/iam.serviceAccountUser` on the runtime SA. Re-run step 5.

**Container crashes on first request with "Could not load credentials".**
The runtime SA is missing one of the data-plane roles. Check the Cloud Run logs for the specific scope (BigQuery vs Firestore vs Secret Manager) and re-grant step 4.

**Healthcheck failing on Cloud Run dashboard.**
Cloud Run probes `/`; FastAPI returns the Jinja landing page so the probe should pass. If `frontend/dist` is missing, `/` still works (Jinja is independent). If `/api/health` is what's failing, check the Firestore IAM binding — the health endpoint pings Firestore at startup.

**`K_SERVICE` not set / no traces in Cloud Trace.**
`K_SERVICE` is set automatically by Cloud Run, so this only matters in local dev. The tracing init in `main.py` no-ops when `K_SERVICE` is absent — that's intentional.

## Future: Vertex AI Agent Engine

The Hosting baseline calls for the conversational agent to run on Vertex AI Agent Engine, not in-process inside FastAPI. That extraction is its own workstream (Phase D in the compliance doc) — `services/gemini.py` becomes a thin adapter, the chat/voice flows are re-shaped as a LangGraph graph, and the agent runs as a managed Vertex deployment with this Cloud Run service as the public-facing API gateway.

For now: the agent runs inside the container. Capacity-planning and quotas at that point will need a second pass.
