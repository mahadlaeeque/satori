# Satori — Build & Delivery Runbook

This document is the runbook for closing the **Build & Delivery** standard (Cloud
Build + Artifact Registry). It also lays the foundation for **Hosting** (Cloud
Run), since the container image we produce here is exactly what Cloud Run will
serve.

## What changed in the code

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage build (Python 3.12 slim → gunicorn as non-root on port 8080). |
| `.dockerignore` | Strips state files, local data, secrets, docs, dev artifacts. |
| `cloudbuild.yaml` | Syntax check → docker build → push two tags (`:$SHORT_SHA` and `:latest`) to Artifact Registry. Cloud Run deploy step left commented out. |
| `deploy.ps1` | One-file PowerShell helper: `build`, `run`, `push`, `logs`. |

## What you need to do — overview

Five things, all listed below in order:

1. Install Docker Desktop (one-time)
2. Put the code in a Git repo (one-time)
3. Create the Artifact Registry repo in GCP (one-time)
4. Smoke-test the image locally
5. Wire up a Cloud Build trigger (one-time)

Steps 1, 2, 3, 5 each happen once. Step 4 you'll do every time you change code.

Total time: ~45 minutes, mostly waiting for installs.

---

## Step 1 — Install Docker Desktop *(one-time, ~10 min)*

Without Docker on your laptop, you can't smoke-test the image before pushing.
You *can* skip this and use Cloud Build for every build, but each cloud build
takes 3–5 minutes, which kills the iteration loop.

1. Download from https://www.docker.com/products/docker-desktop/ (Windows).
2. Run the installer. When prompted about WSL2, accept the defaults.
3. Reboot if asked.
4. Start Docker Desktop. Wait until the whale icon in the system tray stops
   animating (it'll say "Engine running").
5. Verify in PowerShell:
   ```powershell
   docker --version
   docker run --rm hello-world
   ```

If `hello-world` prints "Hello from Docker!", you're good.

---

## Step 2 — Put the code in a Git repo *(one-time, ~10 min)*

Cloud Build needs a Git repo somewhere it can watch. GitHub is the easiest path.

### 2a. Initialise the repo locally *(PowerShell)*

```powershell
cd "C:\Users\Hp\Desktop\Claude - Satori\satori-chatbot"
git init
git add .
git commit -m "Initial Satori commit"
```

> If `git` says "command not found", install **Git for Windows** from
> https://gitforwindows.org/ first.

### 2b. Create a private GitHub repo *(Browser)*

1. Go to https://github.com/new
2. Repository name: `satori`
3. Set it to **Private**
4. **Do not** initialise with a README, .gitignore, or license — your folder
   already has files.
5. Click **Create repository**.
6. On the next page, copy the two lines under
   "**…or push an existing repository from the command line**". They look
   like:
   ```
   git remote add origin https://github.com/<your-user>/satori.git
   git branch -M main
   git push -u origin main
   ```
7. Run those three lines in PowerShell. You may be asked to authenticate —
   accept the browser prompt.

### 2c. Add a sensible `.gitignore` *(PowerShell)*

Don't commit secrets or local state:

```powershell
@"
__pycache__/
*.pyc
.venv/
venv/
chat_history.json
settings.json
data/
service-account/
*.log
.env
.env.*
"@ | Out-File -FilePath .gitignore -Encoding utf8

git rm --cached chat_history.json settings.json 2>$null
git add .gitignore
git commit -m "Add .gitignore"
git push
```

---

## Step 3 — Create the Artifact Registry repo *(one-time, ~2 min)*

This is the bucket-equivalent for container images.

### Via gcloud *(PowerShell)*

```powershell
gcloud artifacts repositories create satori `
    --project=ai-vertex-mahad `
    --location=us-central1 `
    --repository-format=docker `
    --description="Satori container images"
```

### Or via Console *(Browser)*

1. https://console.cloud.google.com/artifacts?project=ai-vertex-mahad
2. **Create repository**
3. Name: `satori`
4. Format: **Docker**
5. Location type: **Region** → **us-central1 (Iowa)**
6. Click **Create**.

### Grant Cloud Build permission to push to it

Cloud Build's default service account needs `roles/artifactregistry.writer`.
This is usually granted automatically, but verify:

```powershell
$PROJECT_NUMBER = (gcloud projects describe ai-vertex-mahad --format='value(projectNumber)')
$CB_SA = "${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

gcloud projects add-iam-policy-binding ai-vertex-mahad `
    --member="serviceAccount:$CB_SA" `
    --role="roles/artifactregistry.writer"
```

---

## Step 4 — Smoke-test the image locally *(every code change, ~2 min)*

```powershell
cd "C:\Users\Hp\Desktop\Claude - Satori\satori-chatbot"
.\deploy.ps1 run
```

That builds and runs the container with your gcloud credentials mounted in.
Open http://localhost:8080 — the app should behave identically to running
`python app.py` directly, with `State: firestore` in the startup banner.

Ctrl+C to stop.

If `deploy.ps1 run` fails, common causes:
- Docker Desktop isn't running → start it from the Start menu.
- `gcloud auth application-default login` hasn't been run on this machine → run it.
- Firewall blocking port 8080 → close anything else on 8080 (`netstat -ano | findstr 8080`).

---

## Step 5 — Wire up a Cloud Build trigger *(one-time, ~10 min)*

This is the magic step: from now on, every push to `main` builds + pushes
automatically.

### 5a. Connect Cloud Build to GitHub *(Browser)*

1. Open https://console.cloud.google.com/cloud-build/triggers?project=ai-vertex-mahad
2. Click **Connect Repository** at the top (or **Manage Repositories** →
   **Connect Repository**).
3. Source: **GitHub (Cloud Build GitHub App)**.
4. Click **Continue**. You'll be redirected to GitHub to authenticate.
5. On GitHub, grant access to your `satori` repo (and only that one).
6. Back in GCP, tick the `satori` repo → **Connect**.

### 5b. Create the trigger *(Browser)*

1. Still in Cloud Build → **Triggers** → **Create Trigger**.
2. Name: `satori-main-build`.
3. Region: **us-central1**.
4. Event: **Push to a branch**.
5. Source: pick the `satori` repo you just connected.
6. Branch: `^main$` (literally — that's a regex).
7. Configuration: **Cloud Build configuration file (yaml or json)**.
8. Location: **Repository**.
9. Cloud Build configuration file location: `cloudbuild.yaml`.
10. Service account: leave as default (`<project-number>@cloudbuild.gserviceaccount.com`).
11. Expand **Advanced** → **Substitution variables** → **Add variable**.
    - Variable: `_TAG`
    - Value: `$SHORT_SHA`
    This makes Git-triggered builds tag each image with the commit SHA. Manual `.\deploy.ps1 push` runs already pass the value themselves.
12. Click **Create**.

### 5c. Test it end-to-end

Make a no-op commit:

```powershell
"# trigger build" | Out-File -Append README.md
git add README.md
git commit -m "Trigger first Cloud Build"
git push
```

Then open https://console.cloud.google.com/cloud-build/builds?project=ai-vertex-mahad
— a build should appear within seconds. Expect 3–5 minutes for the first build
(subsequent builds are faster due to layer caching).

When it goes green:

```powershell
gcloud artifacts docker images list `
    us-central1-docker.pkg.dev/ai-vertex-mahad/satori
```

You should see your `satori-web` image with both `<commit-sha>` and `latest` tags.

---

## Optional — Manual cloud build without a Git push

Useful when iterating on cloudbuild.yaml:

```powershell
.\deploy.ps1 push
```

Behind the scenes this runs `gcloud builds submit` against your local working
directory and uses your latest commit SHA as the tag.

---

## What this unlocks

Once Step 5 is green, you've met the **Build & Delivery** standard. From now on:

- Every push to `main` produces a versioned image in Artifact Registry.
- You can roll back any deploy by pointing Cloud Run at an older image tag.
- The Dockerfile is the same one Cloud Run will use, so Hosting (Phase P1)
  becomes a 1-day job instead of two weeks.

The next standard, **Hosting**, is essentially "provision a Cloud Run service
that points at the image this pipeline produces, then uncomment the deploy step
at the bottom of `cloudbuild.yaml`." Ready when you are.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `docker: command not found` | Docker Desktop isn't installed or isn't running (Step 1). |
| `denied: Permission "artifactregistry.repositories.uploadArtifacts" denied` | Cloud Build service account is missing the `roles/artifactregistry.writer` role (Step 3 sub-section). |
| `failed to authorize: failed to fetch oauth token` during local push | You ran `docker push` directly without `gcloud auth configure-docker us-central1-docker.pkg.dev`. Run that one-liner once. |
| `Service Unavailable: 503` when hitting localhost:8080 | Container hasn't finished booting (gunicorn takes ~3s). Wait, retry. |
| First Cloud Build fails on "no such image" cache pull | Expected — `--cache-from` is best-effort. The next build will hit cache. |
| Build is slow (>10 min) | First build always is. After that, only changed layers rebuild. |
| Image is huge (>1.5 GB) | Check `.dockerignore` is being applied: `docker build --no-cache --progress=plain .` and look for files being copied that shouldn't be. |
