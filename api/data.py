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
# /api/attendance-analytics — comprehensive dashboard payload
# =====================================================================
@router.get("/api/attendance-analytics")
async def get_attendance_analytics(req: Request):
    return to_response(_get_attendance_analytics(FlaskReq(req)))


def _get_attendance_analytics(request):
    """One round-trip dashboard payload: KPIs, daily trend, dept breakdown,
    day-of-week, check-in distribution, top absentees/late, insights.

    Filters: range (days, default 30), department, employee, date_from, date_to.
    Department resolves via JOIN onto Employee_Data.Employee_Hierarchy.
    """
    settings = load_settings()
    project = settings["gcp_project"]
    dataset = settings["bq_dataset"]
    att = f"`{project}.{dataset}.Attendance_Data`"
    emp = f"`{project}.{dataset}.Employee_Data`"

    # ── Parse filters ──────────────────────────────────────────────────
    days = int(request.args.get("range", 30) or 30)
    days = max(1, min(days, 365))
    date_from = request.args.get("date_from", "").strip()
    date_to   = request.args.get("date_to",   "").strip()
    department = request.args.get("department", "").strip()
    employee   = request.args.get("employee", "").strip()

    # Build the WHERE clauses for the attendance side
    where = []
    if date_from:
        where.append(f"a.attendance_date >= DATE('{date_from}')")
    elif days:
        where.append(f"a.attendance_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {days} DAY)")
    if date_to:
        where.append(f"a.attendance_date <= DATE('{date_to}')")

    where.append("a.attendance_date IS NOT NULL")

    if employee:
        safe_emp = employee.replace("'", "\\'").lower()
        where.append(f"LOWER(CAST(a.employee_name AS STRING)) LIKE '%{safe_emp}%'")

    # Department filter requires the JOIN
    needs_emp_join = bool(department)
    join_clause = ""
    if needs_emp_join:
        safe_dept = department.replace("'", "\\'")
        join_clause = f"LEFT JOIN {emp} e ON CAST(e.Employee_Code AS STRING) = CAST(a.employee_id AS STRING)"
        where.append(f"COALESCE(NULLIF(TRIM(e.Employee_Hierarchy), ''), 'Unspecified') = '{safe_dept}'")

    where_str = " AND ".join(where) if where else "1=1"

    try:
        client = get_bq_client(settings)

        # ── 1. Summary KPIs ─────────────────────────────────────────────
        kpi_sql = f"""
        SELECT
          COUNT(*)                                              AS total_records,
          COUNT(DISTINCT a.employee_id)                         AS unique_employees,
          SUM(a.is_present)                                     AS present_days,
          SUM(a.is_absent)                                      AS absent_days,
          SUM(a.is_on_leave)                                    AS leave_days,
          SUM(a.is_remote)                                      AS remote_days,
          SUM(a.is_holiday)                                     AS holiday_days,
          SUM(a.is_weekend)                                     AS weekend_days,
          COUNTIF(LOWER(a.attendance_status_text) = 'late')     AS late_count,
          ROUND(100.0 * SUM(a.is_present) / NULLIF(COUNT(*),0), 1) AS attendance_rate,
          ROUND(100.0 * COUNTIF(LOWER(a.attendance_status_text) = 'late')
                / NULLIF(SUM(a.is_present),0), 1)               AS late_rate
        FROM {att} a
        {join_clause}
        WHERE {where_str}
        """
        kpi_row = list(client.query(kpi_sql).result())
        kpi: Dict[str, Any] = {}
        if kpi_row:
            r = kpi_row[0]
            kpi = {
                "total_records":    int(r.total_records or 0),
                "unique_employees": int(r.unique_employees or 0),
                "present_days":     int(r.present_days or 0),
                "absent_days":      int(r.absent_days or 0),
                "leave_days":       int(r.leave_days or 0),
                "remote_days":      int(r.remote_days or 0),
                "holiday_days":     int(r.holiday_days or 0),
                "weekend_days":     int(r.weekend_days or 0),
                "late_count":       int(r.late_count or 0),
                "attendance_rate":  float(r.attendance_rate or 0),
                "late_rate":        float(r.late_rate or 0),
                "on_time_rate":     round(100.0 - float(r.late_rate or 0), 1),
            }

        # ── 2. Daily trend ──────────────────────────────────────────────
        trend_sql = f"""
        SELECT
          a.attendance_date AS date,
          COUNT(*)                                                 AS total,
          SUM(a.is_present)                                        AS present,
          SUM(a.is_absent)                                         AS absent,
          COUNTIF(LOWER(a.attendance_status_text) = 'late')        AS late,
          ROUND(100.0 * SUM(a.is_present) / NULLIF(COUNT(*),0), 1) AS rate
        FROM {att} a
        {join_clause}
        WHERE {where_str}
        GROUP BY a.attendance_date
        ORDER BY a.attendance_date
        """
        daily_trend = [{
            "date":    str(r.date),
            "total":   int(r.total or 0),
            "present": int(r.present or 0),
            "absent":  int(r.absent or 0),
            "late":    int(r.late or 0),
            "rate":    float(r.rate or 0),
        } for r in client.query(trend_sql).result()]

        # ── 3. Day-of-week breakdown ────────────────────────────────────
        dow_sql = f"""
        SELECT
          FORMAT_DATE('%A', a.attendance_date)                     AS weekday,
          EXTRACT(DAYOFWEEK FROM a.attendance_date)                AS dow_num,
          COUNT(*)                                                 AS total,
          SUM(a.is_present)                                        AS present,
          COUNTIF(LOWER(a.attendance_status_text) = 'late')        AS late,
          ROUND(100.0 * SUM(a.is_present) / NULLIF(COUNT(*),0), 1) AS rate,
          ROUND(100.0 * COUNTIF(LOWER(a.attendance_status_text) = 'late')
                / NULLIF(SUM(a.is_present),0), 1)                  AS late_pct
        FROM {att} a
        {join_clause}
        WHERE {where_str} AND a.is_weekend = 0 AND a.is_holiday = 0
        GROUP BY weekday, dow_num
        ORDER BY dow_num
        """
        day_of_week = [{
            "weekday":  r.weekday,
            "rate":     float(r.rate or 0),
            "late_pct": float(r.late_pct or 0),
            "present":  int(r.present or 0),
            "late":     int(r.late or 0),
        } for r in client.query(dow_sql).result()]

        # ── 4. Check-in hour distribution ───────────────────────────────
        hour_sql = f"""
        SELECT
          EXTRACT(HOUR FROM SAFE_CAST(a.checkin_time AS TIMESTAMP)) AS hour,
          COUNT(*) AS cnt
        FROM {att} a
        {join_clause}
        WHERE {where_str} AND a.is_present = 1 AND a.checkin_time IS NOT NULL
        GROUP BY hour
        ORDER BY hour
        """
        checkin_dist = [{
            "hour": int(r.hour),
            "count": int(r.cnt or 0),
        } for r in client.query(hour_sql).result() if r.hour is not None]

        # ── 5. Department breakdown — always with the JOIN ──────────────
        dept_where = [w for w in where if "Employee_Hierarchy" not in w]
        dept_where_str = " AND ".join(dept_where) if dept_where else "1=1"
        dept_sql = f"""
        SELECT
          COALESCE(NULLIF(TRIM(e.Employee_Hierarchy), ''), 'Unspecified') AS department,
          COUNT(DISTINCT a.employee_id)                                   AS employees,
          COUNT(*)                                                        AS total,
          SUM(a.is_present)                                               AS present,
          COUNTIF(LOWER(a.attendance_status_text) = 'late')               AS late,
          ROUND(100.0 * SUM(a.is_present) / NULLIF(COUNT(*),0), 1)        AS rate
        FROM {att} a
        LEFT JOIN {emp} e ON CAST(e.Employee_Code AS STRING) = CAST(a.employee_id AS STRING)
        WHERE {dept_where_str}
        GROUP BY department
        HAVING COUNT(*) > 5
        ORDER BY employees DESC
        LIMIT 12
        """
        dept_breakdown = [{
            "department": r.department,
            "employees":  int(r.employees or 0),
            "rate":       float(r.rate or 0),
            "late":       int(r.late or 0),
            "total":      int(r.total or 0),
        } for r in client.query(dept_sql).result()]

        # ── 6. Top absentees ────────────────────────────────────────────
        abs_sql = f"""
        SELECT
          a.employee_name AS name,
          COUNT(*)        AS absent_days
        FROM {att} a
        {join_clause}
        WHERE {where_str} AND a.is_absent = 1 AND a.employee_name IS NOT NULL
        GROUP BY a.employee_name
        ORDER BY absent_days DESC
        LIMIT 10
        """
        top_absent = [{
            "name": r.name,
            "absent_days": int(r.absent_days or 0),
        } for r in client.query(abs_sql).result()]

        # ── 7. Top late arrivals ────────────────────────────────────────
        late_sql = f"""
        SELECT
          a.employee_name        AS name,
          COUNT(*)               AS late_count
        FROM {att} a
        {join_clause}
        WHERE {where_str}
          AND LOWER(a.attendance_status_text) = 'late'
          AND a.employee_name IS NOT NULL
        GROUP BY a.employee_name
        ORDER BY late_count DESC
        LIMIT 10
        """
        top_late = [{
            "name": r.name,
            "late_count": int(r.late_count or 0),
        } for r in client.query(late_sql).result()]

        # ── 8. Rule-based insights ──────────────────────────────────────
        insights: List[Dict[str, Any]] = []
        if kpi.get("attendance_rate", 0) >= 95:
            insights.append({
                "severity": "good",
                "title": "Strong attendance",
                "body": f"Attendance rate is {kpi['attendance_rate']}% — comfortably above the 90% healthy band.",
            })
        elif kpi.get("attendance_rate", 0) < 85:
            insights.append({
                "severity": "warning",
                "title": "Attendance below threshold",
                "body": f"Attendance rate is {kpi['attendance_rate']}%. Investigate top absentees and recent trend.",
            })

        if kpi.get("late_rate", 0) > 12:
            insights.append({
                "severity": "warning",
                "title": "Elevated late arrivals",
                "body": f"{kpi['late_rate']}% of present days were late — typical band is under 10%.",
            })

        if day_of_week:
            worst = min(day_of_week, key=lambda x: x["rate"])
            best = max(day_of_week, key=lambda x: x["rate"])
            if worst["rate"] < best["rate"] - 5:
                insights.append({
                    "severity": "info",
                    "title": f"{worst['weekday']} is the weakest day",
                    "body": f"{worst['weekday']} attendance is {worst['rate']}% vs {best['weekday']} at {best['rate']}%. "
                            f"Consider what's different that day.",
                })

        if checkin_dist:
            peak = max(checkin_dist, key=lambda x: x["count"])
            insights.append({
                "severity": "info",
                "title": "Peak check-in hour",
                "body": f"Most check-ins happen at {peak['hour']:02d}:00 ({peak['count']:,} records).",
            })

        if top_absent and top_absent[0]["absent_days"] >= 5:
            insights.append({
                "severity": "warning",
                "title": "Persistent absentee",
                "body": f"{top_absent[0]['name']} has {top_absent[0]['absent_days']} absences in the selected window — "
                        f"may warrant a manager conversation.",
            })

        return jsonify({
            "summary":         kpi,
            "daily_trend":     daily_trend,
            "day_of_week":     day_of_week,
            "checkin_dist":    checkin_dist,
            "dept_breakdown":  dept_breakdown,
            "top_absent":      top_absent,
            "top_late":        top_late,
            "insights":        insights,
            "filters": {
                "range":      days,
                "date_from":  date_from,
                "date_to":    date_to,
                "department": department,
                "employee":   employee,
            },
        })

    except Exception as e:
        traceback.print_exc()
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
