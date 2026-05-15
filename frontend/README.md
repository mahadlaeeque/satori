# Satori — React Frontend

Vite + React 18 + TypeScript 5 + Tailwind 3 + TanStack Query 5.

## Dev loop

```bash
cd frontend
npm install
npm run dev
```

Vite starts on http://localhost:5173. API requests (`/api/*`, `/ask`, `/history`,
`/settings`) are proxied to the FastAPI backend on :8080 — so you also need
the backend running in another terminal:

```bash
# in a separate terminal, at the satori-chatbot root
$env:SATORI_STATE_BACKEND = "firestore"
uvicorn main:app --port 8080
```

## Production build

```bash
cd frontend
npm run build         # outputs frontend/dist
```

FastAPI mounts `frontend/dist` at `/app/*` automatically (see main.py). After
building, restart the FastAPI server and open http://localhost:8080/app to see
the new UI. The legacy Jinja UI continues to be served at http://localhost:8080/
during the migration.

## File layout

```
frontend/
├── package.json
├── vite.config.ts        # Dev proxy + /app base path
├── tsconfig.json
├── tailwind.config.js
├── postcss.config.js
├── index.html
└── src/
    ├── main.tsx          # React entry, QueryClient, BrowserRouter(basename=/app)
    ├── App.tsx           # Layout + Routes
    ├── index.css         # Tailwind + base styles + component classes
    ├── api/
    │   ├── types.ts      # TypeScript interfaces mirroring FastAPI responses
    │   ├── client.ts     # fetch wrapper with ApiError
    │   └── hooks.ts      # TanStack Query hooks (one per logical query/mutation)
    ├── components/
    │   ├── Sidebar.tsx
    │   ├── TopHeader.tsx
    │   └── StarRow.tsx
    └── panels/
        ├── ChatPanel.tsx              # /chat — Ask Me Anything
        ├── CapabilityMatrixPanel.tsx  # /matrix — workforce capability scoring
        └── PlaceholderPanel.tsx       # stand-in for not-yet-migrated panels
```

## Migration status

Migrated to React:

- ✅ Capability Intelligence Matrix
- ✅ Ask Me Anything (chat)

Still on the legacy Jinja UI:

- Availability Engine (with Create Task / AI staffing modal)
- Predictive Patterns (with per-employee modal + AI productivity tips)
- Attendance charts
- Timesheets data table
- Resource Allocation data table
- Employee Data
- Account Coverage / Pipeline Health / AM Scorecard / KPI Scorecard
- Voice (Gemini Live API)
- Skill Enrichment

The legacy UI is available at http://localhost:8080/ during migration. Once
all panels are ported, swap the `@app.get("/")` route in main.py from
`templates.TemplateResponse("index.html", ...)` to `RedirectResponse("/app/")`.

## Pattern for adding a new panel

1. Add a route in App.tsx between ChatPanel and PlaceholderPanel imports
2. Create `src/panels/MyPanel.tsx` — use the TanStack hook from `api/hooks.ts`
3. Add the NavLink in `Sidebar.tsx`
4. Tailwind utilities for layout; reuse `.card`, `.kpi`, `.input`, `.pill`,
   `.btn-primary`, `.btn-ghost` from `index.css`

## Adding a new API endpoint

1. Define the TypeScript response type in `src/api/types.ts`
2. Add a TanStack hook in `src/api/hooks.ts`
3. Use it in your component
