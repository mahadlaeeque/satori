"""
Satori Live Voice Agent
========================
Direct browser-to-Gemini Live API architecture:

  Browser ──WebSocket──▸ Gemini Multimodal Live API (audio in/out)
  Browser ──REST POST──▸ Flask /api/voice/query (BigQuery tool calls)

The backend provides:
  1. /api/voice/session — returns config needed to connect (API key, voice, system prompt, tools)
  2. /api/voice/query  — executes BigQuery SQL for tool calls
"""

import json
import logging
import requests as http_requests
from flask import request, jsonify

logger = logging.getLogger(__name__)


# ── System instruction for the live voice session ──────────────────────
def _build_system_instruction(settings):
    tables = settings.get("bq_tables", [])
    project = settings.get("gcp_project", "")
    dataset = settings.get("bq_dataset", "")

    schema_parts = []
    table_names = []
    for t in tables:
        desc = t.get("schema_description", "")
        fqn = f"{project}.{dataset}.{t['table_name']}"
        table_names.append(fqn)
        if desc and desc != "AUTO_DETECT":
            schema_parts.append(f"Table: {fqn}\n{desc}")

    schema_text = "\n\n".join(schema_parts)
    table_names_str = ", ".join(table_names)
    sql_rules = settings.get("sql_rules", "")

    return f"""You are Satori, TMC's Capability Intelligence Agent in a live voice call.

PERSONALITY & STYLE:
- Warm, professional, concise — this is a voice conversation, not a document.
- Keep answers to 2-3 sentences unless the user explicitly asks for more detail.
- Use specific numbers, names, and dates.
- You are fully bilingual in English and Urdu. Respond in whichever language the user speaks.
  If the user switches language mid-conversation, switch with them.
- Round numbers for speech: say "about 85 percent" not "84.7826 percent".
- Format times in 12-hour format when speaking: "nine thirty AM", not "09:30".
- Never mention SQL, column names, or table names to the user.

TOOL USE (CRITICAL):
You MUST use the query_database tool for ANY question about employees, attendance, timesheets,
projects, allocations, sales, accounts, pipeline, revenue, or workforce data.
NEVER try to answer data questions from memory. ALWAYS call query_database with a valid SQL query.
Even for simple questions like "how many employees do we have" — you MUST query the database.

DATABASE SCHEMAS:
{schema_text}

TABLE SELECTION GUIDE (CRITICAL — choose the right table):

WORKFORCE TABLES:
- {project}.{dataset}.Attendance_Data: For ALL attendance queries (recent and historical). Has: attendance_date, employee_id, employee_name, employee_email, checkin_time (STRING), checkout_time (STRING), attendance_status_text, is_present, is_absent, is_on_leave, is_remote, leave_type_name, etc. Does NOT have location — for location queries JOIN with Employee_Data. IMPORTANT: Always use Attendance_Data (never just "Attendance").
- {project}.{dataset}.Timesheet_Data: For tickets, project hours, logged hours, task descriptions, ticket status, project codes. Has: TICKET_USER_ID, TICKET_NUMBER, TICKET_PROJECT_LABEL, TICKET_HOURS (STRING — SAFE_CAST to FLOAT64), TICKET_STATUS, DATE_KEY, TICKET_DESCRIPTION, TICKET_SUBJECT.
- {project}.{dataset}.Allocation_data: For project assignments, allocation percentages, bench status, competency. Has: project_id, employee_id (STRING "E-1234"), allocation_percent, emp_competency, Flag, Date.
- {project}.{dataset}.Employee_Data: For employee profiles, positions, hierarchy, locations, employment type. Has: Employee_Code ("E-2141"), Resource_Name, Employee_Position, Employee_Email, Employee_Hierarchy, Employee_Location (city), Employee_Status, Employee_Type. Does NOT have employee_id.

SALES / ACCOUNT COVERAGE TABLES:
- {project}.{dataset}.Sales_Accounts: Customer accounts, tiers (A/B/C), visit counts, dormant flags. ~359 rows. Has: VP, AM, Location, Account, Tier, Dormant, Jan_Visits, Feb_Visits, Mar_Visits, Q1_Visits, Zero_Visit.
- {project}.{dataset}.Sales_AM_Scorecard: AM performance — targets, pipeline, win rate. Has: VP, AM, Role, City, col_2026_Target (USD), Q1_ACH (USD), Open_Pipeline (USD), Hist_Win_Rate (decimal 0-1). 8 AMs total.
- {project}.{dataset}.Sales_Plan_vs_Pipeline: Revenue plan vs actual. Has: AM, col_2026_Target, Q1_Target, Q1_ACH, CRM_Pipeline, Coverage_Ratio, Status, Action.
- {project}.{dataset}.Sales_Pipeline_Health: All salespeople pipeline. Has: Salesperson, Open_Pipeline (USD), Open_Deals, Win_Rate_by (decimal 0-1).
- {project}.{dataset}.Sales_Hunting_Gap: New business quotas per AM.
- {project}.{dataset}.Sales_KPI_Scorecard: KPI definitions (reference only).
- {project}.{dataset}.Sales_Dormant_Accounts: Dormant accounts for removal. ~21 accounts.
- {project}.{dataset}.Sales_Workload_Feasibility: AM field capacity — required vs available field days, utilisation.

JOIN KEYS:
- Attendance.employee_name = Employee_Data.Resource_Name (for location/city filtering)
- Allocation_data.employee_id (format "E-1234") = Employee_Data.Employee_Code
- Sales tables join on AM name across Sales_Accounts, Sales_AM_Scorecard, Sales_Plan_vs_Pipeline, Sales_Hunting_Gap, Sales_Workload_Feasibility
- Sales tables do NOT join with Attendance/Timesheet/Employee tables

SQL RULES:
- ONLY write SELECT statements. No INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE.
- ALWAYS use fully qualified table names: {project}.{dataset}.TableName
- checkin_time and checkout_time are STRING columns — ALWAYS use SAFE_CAST(checkin_time AS TIMESTAMP)
- Late check: EXTRACT(TIME FROM SAFE_CAST(checkin_time AS TIMESTAMP)) > TIME(9, 30, 0)
- Working hours: TIMESTAMP_DIFF(SAFE_CAST(checkout_time AS TIMESTAMP), SAFE_CAST(checkin_time AS TIMESTAMP), MINUTE) / 60.0
- attendance_date is DATE. Filter: attendance_date = DATE('2026-04-24')
- TICKET_HOURS is STRING — use SAFE_CAST(TICKET_HOURS AS FLOAT64)
- Employee name matching: ALWAYS use LOWER(name) LIKE LOWER('%partial%')
- For "today" use CURRENT_DATE(), "yesterday" use DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
- Dollar amounts are raw FLOAT64 — format as currency in your spoken response
- Win rates are decimals 0-1 — multiply by 100 for percentage when speaking
- Limit to 20 rows unless aggregation or user asks for more
- Employee_Data does NOT have employee_id — use Resource_Name or Employee_Code

{sql_rules}

Available tables: {table_names_str}"""


# ── Tool declarations ──────────────────────────────────────────────────
def _build_tools():
    return [{
        "functionDeclarations": [{
            "name": "query_database",
            "description": (
                "Execute a BigQuery SELECT query against TMC's workforce and "
                "sales database. Use the table schemas in your system instruction "
                "to write correct SQL."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "sql": {
                        "type": "STRING",
                        "description": "A BigQuery-compatible SELECT query."
                    }
                },
                "required": ["sql"]
            }
        }]
    }]


# ── Register REST routes ───────────────────────────────────────────────
def register_voice_routes(app):
    """Attach voice-agent REST endpoints to the Flask app."""

    # Cache the discovered live model name (avoids re-probing every call)
    _live_model_cache = {"model": None}

    def _discover_live_model(api_key):
        """Probe the Gemini API for available Live API models."""
        if _live_model_cache["model"]:
            return _live_model_cache["model"]

        # Preference order for live models
        preferred = [
            "models/gemini-2.5-flash-live-preview",
            "models/gemini-2.0-flash-live-001",
            "models/gemini-3.1-flash-live-preview",
        ]

        try:
            resp = http_requests.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                timeout=10
            )
            if resp.status_code == 200:
                all_models = [m.get("name", "") for m in resp.json().get("models", [])]
                logger.info("Available models: %d total", len(all_models))

                # Check preferred models first
                for pm in preferred:
                    if pm in all_models:
                        _live_model_cache["model"] = pm
                        logger.info("Using preferred live model: %s", pm)
                        return pm

                # Fallback: find any model with "live" in the name
                live_models = [m for m in all_models if "live" in m.lower()]
                if live_models:
                    _live_model_cache["model"] = live_models[0]
                    logger.info("Using discovered live model: %s", live_models[0])
                    return live_models[0]

                logger.warning("No live models found. Available: %s",
                               [m for m in all_models if "flash" in m.lower()][:10])
        except Exception as e:
            logger.warning("Model probe failed: %s", e)

        # Default fallback
        fallback = preferred[0]
        logger.info("Falling back to default model: %s", fallback)
        return fallback

    @app.route("/api/voice/session", methods=["POST"])
    def voice_session_config():
        """Return everything the browser needs to connect to Gemini Live API."""
        from app import load_settings
        settings = load_settings()
        api_key = settings.get("gemini_api_key", "")
        voice = settings.get("gemini_tts_voice", "Leda")

        if not api_key:
            return jsonify({"error": "No Gemini API key configured."}), 500

        # Auto-detect the correct live model
        model = _discover_live_model(api_key)
        logger.info("Voice session — model: %s, voice: %s", model, voice)

        return jsonify({
            "apiKey": api_key,
            "model": model,
            "voice": voice,
            "systemInstruction": _build_system_instruction(settings),
            "tools": _build_tools()
        })

    @app.route("/api/voice/test", methods=["POST"])
    def voice_test():
        """Test whether the API key can access Gemini Live API models."""
        from app import load_settings
        settings = load_settings()
        api_key = settings.get("gemini_api_key", "")
        if not api_key:
            return jsonify({"ok": False, "error": "No API key configured"}), 400

        # Try to list available models and find live-capable ones
        try:
            resp = http_requests.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                timeout=10
            )
            if resp.status_code != 200:
                return jsonify({
                    "ok": False,
                    "error": f"API returned {resp.status_code}: {resp.text[:300]}"
                })

            models_data = resp.json()
            all_models = [m.get("name", "") for m in models_data.get("models", [])]
            # Find models that support live/bidi streaming
            live_models = [m for m in all_models if "live" in m.lower()]
            # Also check for models that support generateContent with audio
            flash_models = [m for m in all_models if "flash" in m.lower() and ("2.5" in m or "2.0" in m)]

            return jsonify({
                "ok": True,
                "live_models": live_models,
                "flash_models": flash_models[:10],
                "total_models": len(all_models)
            })
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})

    @app.route("/api/voice/query", methods=["POST"])
    def voice_query():
        """Execute a BigQuery SQL query for voice tool calls."""
        from app import load_settings
        from google.cloud import bigquery

        data = request.get_json()
        sql = data.get("sql", "").strip()

        if not sql:
            return jsonify({"result": "No SQL provided."}), 400

        # Validate — SELECT only
        sql_upper = sql.upper().strip()
        if not sql_upper.startswith("SELECT"):
            return jsonify({"result": "Error: Only SELECT queries are allowed."}), 400

        dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "MERGE"]
        for word in dangerous:
            if word in sql_upper.split():
                return jsonify({"result": f"Error: {word} statements are not allowed."}), 400

        settings = load_settings()
        logger.info("Voice query — SQL: %s", sql[:200])

        try:
            bq = bigquery.Client(project=settings["gcp_project"])
            df = bq.query(sql).to_dataframe()

            if len(df) == 0:
                result = "No records found matching this query."
            elif len(df) <= 30:
                result = df.to_string(index=False)
            else:
                result = (
                    df.head(30).to_string(index=False)
                    + f"\n... ({len(df)} total rows, showing first 30)"
                )

            logger.info("Voice query — returned %d rows", len(df))
            return jsonify({"result": result})

        except Exception as e:
            logger.error("Voice query failed: %s", e)
            return jsonify({"result": f"Query error: {e}"}), 500
