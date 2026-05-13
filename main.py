"""
Satori — FastAPI entrypoint
============================
Mounts every router from api/. Serves the existing Jinja template + static
assets so the legacy frontend keeps working unchanged while the React
rewrite happens in Phase C.

Run locally:
    uvicorn main:app --reload --port 8080

Run for production (in Dockerfile / Cloud Run):
    gunicorn -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8080 main:app
"""

from __future__ import annotations
import logging
import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api import (
    chat,
    settings as settings_router,
    system,
    data,
    analytics,
    voice,
)

log = logging.getLogger("satori")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(
    title="Satori",
    description="TMC Capability Intelligence platform",
    version="2.0.0",
)

# Static + templates (preserves the legacy frontend during Phase B/C)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


# ── Mount routers ─────────────────────────────────────────────────────────
app.include_router(system.router)
app.include_router(settings_router.router)
app.include_router(chat.router)
app.include_router(data.router)
app.include_router(analytics.router)
app.include_router(voice.router)


# ── Startup banner ────────────────────────────────────────────────────────
@app.on_event("startup")
def startup_banner():
    try:
        from services import state
        from firestore_client import store
        s = state.load_settings()
        tables = ", ".join(t["table_name"] for t in s.get("bq_tables", []))
        print("\n" + "=" * 55)
        print("   SATORI CAPABILITY INTELLIGENCE PLATFORM (FastAPI)")
        print("=" * 55)
        print(f"   Project:  {s.get('gcp_project','?')}")
        print(f"   Dataset:  {s.get('bq_dataset','?')}")
        print(f"   Tables:   {tables}")
        print(f"   Model:    {s.get('gemini_model','?')}")
        print(f"   State:    {store.mode}")
        print(f"   URL:      http://localhost:{os.environ.get('PORT','8080')}")
        print("=" * 55 + "\n")
    except Exception as e:
        log.warning("Startup banner failed: %s", e)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), reload=False)
