"""Data endpoints: attendance/table fetch, chart aggregations, help bubble."""
from __future__ import annotations
import math
import traceback
from typing import Any, Dict
from fastapi import APIRouter, Request
from google import genai
from google.genai import types

from services import state
from services.gemini import (
    get_bq_client, get_genai_client, build_full_table_name, build_full_table,
)
from api._compat import FlaskReq, jsonify, adapt_body, to_response

load_settings = state.load_settings

router = APIRouter()


# =====================================================================
# /api/attendance — generic table fetch with pagination + filters
# =====================================================================
@router.get("/api/attendance")
async def get_attendance(req: Request):
    return to_response(_get_attendance(FlaskReq(req)))


def _get_attendance(request):
    """Fetch data from any BigQuery table with filters and pagination."""
    settings = load_settings()
    tables = settings.get("bq_tables", [])
    # Allow table selection via query param; default to first table
    selected_table = request.args.get("table", "").strip()
    if not selected_table and tables:
        selected_table = tables[0]["table_name"]
    full_table = build_full_table_name(settings, selected_table)

    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    offset = (page - 1) * per_page

    # Generic text search filter
    search = request.args.get("search", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    # Legacy filters (still work for attendance table)
    employee = request.args.get("employee", "").strip()
    status = request.args.get("status", "").strip()

    where_clauses = []
    if employee:
        where_clauses.append(f"LOWER(CAST(employee_name AS STRING)) LIKE '%{employee.lower()}%'")
    if status:
        where_clauses.append(f"LOWER(CAST(attendance_status_text AS STRING)) = '{status.lower()}'")
    if search:
        where_clauses.append(f"TO_JSON_STRING(t) LIKE '%{search}%'")
    if date_from:
        # Try common date column names
        where_clauses.append(f"DATE(COALESCE(attendance_date, CURRENT_DATE())) >= '{date_from}'")
    if date_to:
        where_clauses.append(f"DATE(COALESCE(attendance_date, CURRENT_DATE())) <= '{date_to}'")

    where_str = " AND ".join(where_clauses) if where_clauses else "1=1"

    try:
        client = get_bq_client(settings)

        # Count query
        count_sql = f"SELECT COUNT(*) as cnt FROM {full_table} t WHERE {where_str}"
        count_result = list(client.query(count_sql).result())
        total = count_result[0].cnt if count_result else 0

        # Data query — SELECT * for generic table support
        data_sql = f"""SELECT * FROM {full_table} t
        WHERE {where_str}
        ORDER BY 1 DESC
        LIMIT {per_page} OFFSET {offset}"""

        results = client.query(data_sql).result()
        # Get column names from schema
        col_names = [field.name for field in results.schema]
        rows = []
        for row in results:
            r = dict(row)
            for k, v in r.items():
                if hasattr(v, 'isoformat'):
                    r[k] = v.isoformat()
                elif v is None:
                    r[k] = None
            rows.append(r)

        return jsonify({
            "rows": rows,
            "columns": col_names,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": math.ceil(total / per_page) if total > 0 else 1,
            "table_name": selected_table
        })
    except Exception as e:
        return jsonify({"error": str(e), "rows": [], "columns": [], "total": 0, "page": 1, "per_page": per_page, "total_pages": 1, "table_name": selected_table}), 500


# =====================================================================
# /api/chart-data — aggregated stats for dashboard charts
# =====================================================================
@router.get("/api/chart-data")
async def get_chart_data(req: Request):
    return to_response(_get_chart_data(FlaskReq(req)))


def _get_chart_data(request):
    """Return aggregated attendance stats for dashboard charts."""
    settings = load_settings()
    project = settings["gcp_project"]
    dataset = settings["bq_dataset"]
    full_table = f"`{project}.{dataset}.Attendance_Data`"

    date_from = request.args.get("date_from", "").strip()
    date_to   = request.args.get("date_to",   "").strip()

    where_clauses = []
    if date_from:
        where_clauses.append(f"attendance_date >= DATE('{date_from}')")
    if date_to:
        where_clauses.append(f"attendance_date <= DATE('{date_to}')")
    where_str = " AND ".join(where_clauses) if where_clauses else "1=1"

    try:
        client = get_bq_client(settings)

        # 1. Status breakdown
        status_sql = f"""
            SELECT attendance_status_text AS status, COUNT(*) AS cnt
            FROM {full_table}
            WHERE {where_str} AND attendance_status_text IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC
        """
        status_rows = [{"status": r.status, "count": r.cnt}
                       for r in client.query(status_sql).result()]

        # 2. Daily trend (last 30 days or filtered range, grouped by date + status)
        trend_limit = ""
        if not date_from and not date_to:
            trend_limit = "AND attendance_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)"
        trend_sql = f"""
            SELECT attendance_date, attendance_status_text AS status, COUNT(*) AS cnt
            FROM {full_table}
            WHERE {where_str} {trend_limit}
              AND attendance_date IS NOT NULL
            GROUP BY 1, 2
            ORDER BY 1
        """
        trend_raw = [{"date": str(r.attendance_date), "status": r.status, "count": r.cnt}
                     for r in client.query(trend_sql).result()]

        # 3. Top 10 most absent employees
        absent_sql = f"""
            SELECT employee_name, COUNT(*) AS absent_days
            FROM {full_table}
            WHERE {where_str} AND is_absent = 1 AND employee_name IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """
        absent_rows = [{"name": r.employee_name, "days": r.absent_days}
                       for r in client.query(absent_sql).result()]

        # 4. Check-in hour distribution (present days only)
        hour_sql = f"""
            SELECT EXTRACT(HOUR FROM SAFE_CAST(checkin_time AS TIMESTAMP)) AS hour,
                   COUNT(*) AS cnt
            FROM {full_table}
            WHERE {where_str} AND is_present = 1 AND checkin_time IS NOT NULL
            GROUP BY 1 ORDER BY 1
        """
        hour_rows = [{"hour": int(r.hour), "count": r.cnt}
                     for r in client.query(hour_sql).result() if r.hour is not None]

        # 5. Attendance rate over time (% present per day)
        rate_limit = ""
        if not date_from and not date_to:
            rate_limit = "AND attendance_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)"
        rate_sql = f"""
            SELECT attendance_date,
                   ROUND(100.0 * SUM(is_present) / COUNT(*), 1) AS rate
            FROM {full_table}
            WHERE {where_str} {rate_limit}
              AND attendance_date IS NOT NULL
            GROUP BY 1 ORDER BY 1
        """
        rate_rows = [{"date": str(r.attendance_date), "rate": float(r.rate)}
                     for r in client.query(rate_sql).result()]

        # 6. Summary KPIs
        kpi_sql = f"""
            SELECT
                COUNT(*) AS total,
                SUM(is_present)  AS present,
                SUM(is_absent)   AS absent,
                SUM(is_on_leave) AS on_leave,
                SUM(is_remote)   AS remote,
                SUM(is_holiday)  AS holiday,
                SUM(is_weekend)  AS weekend,
                ROUND(100.0 * SUM(is_present) / NULLIF(COUNT(*),0), 1) AS attendance_rate,
                COUNT(DISTINCT employee_id) AS unique_employees
            FROM {full_table}
            WHERE {where_str}
        """
        kpi_r = list(client.query(kpi_sql).result())
        kpi = {}
        if kpi_r:
            row = kpi_r[0]
            kpi = {
                "total": int(row.total or 0),
                "present": int(row.present or 0),
                "absent": int(row.absent or 0),
                "on_leave": int(row.on_leave or 0),
                "remote": int(row.remote or 0),
                "holiday": int(row.holiday or 0),
                "weekend": int(row.weekend or 0),
                "attendance_rate": float(row.attendance_rate or 0),
                "unique_employees": int(row.unique_employees or 0)
            }

        return jsonify({
            "status_breakdown": status_rows,
            "daily_trend": trend_raw,
            "top_absent": absent_rows,
            "checkin_hours": hour_rows,
            "attendance_rate": rate_rows,
            "kpi": kpi
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =====================================================================
# GEMINI HELP ENDPOINT
# =====================================================================


# =====================================================================
# /api/help — Gemini-powered help bubble
# =====================================================================
@router.post("/api/help")
async def satori_help(req: Request):
    body = await adapt_body(req)
    return to_response(_satori_help(FlaskReq(req, body)))


def _satori_help(request):
    settings = load_settings()
    data = request.get_json(force=True) or {}
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"answer": "Please ask a question about how to use Satori."}), 400

    system_prompt = """You are Satori Help, an expert assistant for the Satori Attendance Intelligence Platform built for TMC (The Millennium Corporation).

Satori is an AI-powered HR analytics platform that integrates with TMC's HR system (ESS) and uses Google BigQuery as its data backend. It is powered by Google Gemini.

KEY FEATURES you can help with:
1. **Attendance Dashboard** — Interactive charts showing attendance trends, status breakdown (Present/Absent/Leave/Holiday/Weekend), top absent employees, check-in hour distribution, and attendance rate over time. Users can filter by date range.
2. **Satori Chat Assistant** — A floating green chat button (bottom-right) where users can ask natural-language questions about attendance, timesheets, or allocation data. Example: "Who had the most absences last month?" or "Show me attendance for Ahmed last week".
3. **Voice Assistant** — Users can speak their questions using the microphone button in the chat panel.
4. **Capability Intelligence Matrix** — Scores employees on Reliability, Project Engagement, and Punctuality using data from all connected tables.
5. **Settings** — Configure the Gemini API key, BigQuery project/dataset, table schemas, scoring weights, and TTS voice preferences.
6. **ESS Sidebar Navigation** — Links to Home, My Profile, My Request, Timesheet, Compensation, Previous Attendance, Document Center, and Satori.

DATA SOURCES:
- Attendance_Data: Daily attendance records (check-in/out times, status flags)
- Timesheet_Data: Project timesheet entries
- Allocation_data: Employee project allocations
- Employee_Data: Employee master records

NAVIGATION:
- Dashboard = /dashboard (charts and insights)
- Chat = / (main Satori chat interface)
- Settings = accessible from chat interface

Answer in a helpful, concise, friendly way. Focus on practical "how to" guidance. If you don't know something specific about the user's setup, give general guidance."""

    try:
        client = get_genai_client(settings)
        response = client.models.generate_content(
            model=settings.get("gemini_model", "gemini-2.5-flash"),
            contents=[
                types.Content(role="user", parts=[types.Part(text=question)])
            ],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.3,
                max_output_tokens=600
            )
        )
        answer = response.text or "I'm not sure how to answer that. Try asking about specific Satori features."
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({"answer": f"Help service unavailable: {str(e)}"}), 500


# =====================================================================
# AVAILABILITY ENGINE
# =====================================================================
