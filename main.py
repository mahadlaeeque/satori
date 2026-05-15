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
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

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


# ── OpenTelemetry tracing ─────────────────────────────────────────────────
# Initialised before the FastAPI app is constructed so the instrumentor can
# wrap the request lifecycle from the very first incoming request.
#
# Activation rule: K_SERVICE is set automatically on Cloud Run. If it's
# present, we wire up the GCP Cloud Trace exporter. Locally K_SERVICE is
# absent and tracing no-ops — no spans are exported, no extra latency, no
# noisy auth errors.
#
# Override knob: SATORI_DISABLE_TRACING=1 forces tracing off even on Cloud
# Run (useful for quick A/B perf comparisons).
def _init_tracing() -> None:
    if not os.environ.get("K_SERVICE"):
        log.debug("Tracing skipped: K_SERVICE not set (running outside Cloud Run).")
        return
    if os.environ.get("SATORI_DISABLE_TRACING") == "1":
        log.info("Tracing skipped: SATORI_DISABLE_TRACING=1.")
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        resource = Resource.create(
            {
                "service.name": os.environ.get("K_SERVICE", "satori-web"),
                "service.version": os.environ.get("K_REVISION", "unknown"),
                "cloud.provider": "gcp",
                "cloud.platform": "gcp_cloud_run",
            }
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter()))
        trace.set_tracer_provider(provider)

        # Instrument outbound HTTP so external calls (Google APIs, etc.)
        # automatically get child spans on the request span.
        RequestsInstrumentor().instrument()

        log.info("Cloud Trace exporter initialised (service=%s).", resource.attributes["service.name"])
    except Exception as exc:  # pragma: no cover — never let tracing break startup
        log.warning("Tracing init failed (continuing without it): %s", exc)


_init_tracing()


app = FastAPI(
    title="Satori",
    description="TMC Capability Intelligence platform",
    version="2.1.0",
)


# Attach the FastAPI instrumentor *after* the app is constructed. If tracing
# wasn't initialised (e.g. local dev), this is a cheap no-op — it just hooks
# the request lifecycle to the global no-op tracer provider.
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
except Exception as exc:  # pragma: no cover
    log.warning("FastAPI instrumentation skipped: %s", exc)

# Static + templates (preserves the legacy frontend during Phase B/C)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


# ── React app — mount only if frontend/dist exists (built via `npm run build`)
_FRONTEND_DIST = os.path.join(BASE_DIR, "frontend", "dist")


class SPAStaticFiles(StaticFiles):
    """StaticFiles that falls back to index.html for any unknown path.

    React Router uses client-side routing — URLs like /app/chat and
    /app/attendance aren't real files on disk, but the React app knows how to
    render them once it boots. Without this fallback, deep-linking or refreshing
    on any sub-route returns a 404 from FastAPI before the React app gets a
    chance to load. With this fallback, every unknown path under /app serves
    index.html so React Router can take over.
    """

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except (HTTPException, StarletteHTTPException) as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


if os.path.isdir(_FRONTEND_DIST):
    app.mount("/app", SPAStaticFiles(directory=_FRONTEND_DIST, html=True), name="react_app")
else:
    @app.get("/app", response_class=HTMLResponse)
    @app.get("/app/{path:path}", response_class=HTMLResponse)
    def react_app_placeholder(path: str = ""):
        return HTMLResponse(
            "<h2>React build not found.</h2>"
            "<p>Run <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code> "
            "from the satori-chatbot directory, then restart the server.</p>",
            status_code=503,
        )


# ── Mount routers ─────────────────────────────────────────────────────────
app.include_router(system.router)
app.include_router(settings_router.router)
app.include_router(chat.router)
app.include_router(data.router)
app.include_router(analytics.router)
app.include_router(voice.router)


# ── Startup banner — runs in a background thread so it cannot block the
# event loop. Firestore + Secret Manager init can take a few seconds; we
# don't want that delaying the listener from accepting connections.
import threading


def _print_startup_banner():
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
        print("=" * 55 + "\n", flush=True)
    except Exception as e:
        log.warning("Startup banner failed: %s", e)


@app.on_event("startup")
def kickoff_banner():
    # Fire and forget - startup completes immediately, banner prints when ready.
    threading.Thread(target=_print_startup_banner, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), reload=False)
