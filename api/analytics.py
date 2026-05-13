"""Analytics endpoints: availability, predictive, capability, AI suggestions."""
from __future__ import annotations
import json
import math
import re
import traceback
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from google import genai
from google.genai import types
from google.cloud import bigquery

from services import state
from services.gemini import (
    get_bq_client, get_genai_client, build_full_table_name, build_full_table,
    get_all_table_names, build_schema_context, _genai_client,
)
from api._compat import FlaskReq, jsonify, adapt_body, to_response

load_settings = state.load_settings

router = APIRouter()


# Internal helpers used by capability + availability ─────────────────────
def _discover_columns(bq_client, project, dataset, table_name):
    """Return set of column names for a table via INFORMATION_SCHEMA."""
    sql = f"""SELECT column_name FROM `{project}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
              WHERE table_name = '{table_name}'"""
    return {row.column_name for row in bq_client.query(sql).result()}


def _find_column(columns, candidates):
    col_lower = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in col_lower:
            return col_lower[cand.lower()]
    return None


def _normalize_eid(raw):
    s = str(raw).strip()
    s = re.sub(r'^[A-Za-z]+-?', '', s)
    s = s.lstrip('0') or '0'
    return s


# =====================================================================
# /api/availability
# =====================================================================
@router.get("/api/availability")
async def availability_engine(req: Request):
    return to_response(_availability_engine(FlaskReq(req)))


def _availability_engine(request):
    """Classify employees as Allocated / Partial / Bench using exact schema columns.

    Joins:
      Employee_Data.Employee_Code = Allocation_data.employee_id  (after normalization)
      Timesheet_Data.TICKET_PROJECT_LABEL -> project context + hours logged
    """
    settings = load_settings()
    project  = settings["gcp_project"]
    dataset  = settings["bq_dataset"]
    emp_table   = f"`{project}.{dataset}.Employee_Data`"
    alloc_table = f"`{project}.{dataset}.Allocation_data`"
    ts_table    = f"`{project}.{dataset}.Timesheet_Data`"

    search            = request.args.get("search", "").strip().lower()
    status_filter     = request.args.get("status", "").strip()   # Allocated|Partial|Bench
    competency_filter = request.args.get("competency", "").strip().lower()

    try:
        client = get_bq_client(settings)

        # ── Allocation summary per employee_id ──────────────────────────────
        # Uses exact columns: employee_id, allocation_percent, emp_competency,
        # project_id, Flag, Forecast_Flag, Date
        alloc_sql = f"""
        SELECT
            CAST(employee_id AS STRING)                             AS emp_id,
            ROUND(AVG(SAFE_CAST(allocation_percent AS FLOAT64)), 1) AS avg_pct,
            MAX(SAFE_CAST(allocation_percent AS FLOAT64))           AS max_pct,
            COUNT(DISTINCT project_id)                              AS proj_count,
            STRING_AGG(DISTINCT CAST(project_id AS STRING), ', ' ORDER BY CAST(project_id AS STRING) LIMIT 8) AS proj_list,
            STRING_AGG(DISTINCT emp_competency, ' | ' ORDER BY emp_competency LIMIT 6) AS competencies,
            MAX(Flag)                                               AS flag,
            MAX(Forecast_Flag)                                      AS forecast_flag,
            MAX(CAST(Date AS STRING))                               AS last_alloc_date,
            COUNTIF(Flag = 'Actual')                                AS actual_weeks,
            COUNTIF(Flag = 'Forecast')                              AS forecast_weeks
        FROM {alloc_table}
        GROUP BY employee_id
        """

        alloc_summary = {}
        for row in client.query(alloc_sql).result():
            key = _normalize_eid(row.emp_id)
            alloc_summary[key] = {
                "avg_pct":        float(row.avg_pct or 0),
                "max_pct":        float(row.max_pct or 0),
                "proj_count":     int(row.proj_count or 0),
                "proj_list":      str(row.proj_list or ""),
                "competencies":   str(row.competencies or ""),
                "flag":           str(row.flag or ""),
                "forecast_flag":  str(row.forecast_flag or ""),
                "last_alloc_date":str(row.last_alloc_date or "")[:10],
                "actual_weeks":   int(row.actual_weeks or 0),
                "forecast_weeks": int(row.forecast_weeks or 0),
            }

        # ── Timesheet hours per project label (last 90 days) ───────────────
        # Uses exact columns: TICKET_PROJECT_LABEL, TICKET_HOURS, DATE_KEY, TICKET_USER_ID
        ts_sql = f"""
        SELECT
            CAST(TICKET_USER_ID AS STRING)                         AS ts_user,
            ROUND(SUM(SAFE_CAST(TICKET_HOURS AS FLOAT64)), 1)      AS total_hours,
            COUNT(DISTINCT TICKET_PROJECT_LABEL)                   AS proj_count_ts,
            STRING_AGG(DISTINCT TICKET_PROJECT_LABEL, ', ' ORDER BY TICKET_PROJECT_LABEL LIMIT 5) AS projects_ts,
            MAX(CAST(DATE_KEY AS STRING))                          AS last_ts_date
        FROM {ts_table}
        WHERE DATE_KEY IS NOT NULL
          AND DATE(DATE_KEY) >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
          AND TICKET_HOURS IS NOT NULL
        GROUP BY TICKET_USER_ID
        """
        ts_map = {}
        for row in client.query(ts_sql).result():
            key = _normalize_eid(row.ts_user)
            ts_map[key] = {
                "total_hours":   float(row.total_hours or 0),
                "proj_count_ts": int(row.proj_count_ts or 0),
                "projects_ts":   str(row.projects_ts or ""),
                "last_ts_date":  str(row.last_ts_date or "")[:10],
            }

        # ── Employee master (exact columns) ─────────────────────────────────
        ALLOWED = {"mto", "permanent", "probation"}
        emp_sql = f"""
        SELECT
            Employee_Code, Resource_Name, Employee_Position,
            Employee_Location, Employee_Status, Employee_Type,
            Employee_Hierarchy, Employee_Email
        FROM {emp_table}
        WHERE LOWER(COALESCE(Employee_Type,'')) IN ('mto','permanent','probation')
        """

        employees = []
        for row in client.query(emp_sql).result():
            name  = str(row.Resource_Name   or "").strip()
            code  = _normalize_eid(row.Employee_Code or "")
            pos   = str(row.Employee_Position or "").strip()
            loc   = str(row.Employee_Location or "").strip()
            hier  = str(row.Employee_Hierarchy or "").strip()
            etype = str(row.Employee_Type or "").strip()
            estatus = str(row.Employee_Status or "").strip()

            al  = alloc_summary.get(code, {})
            ts  = ts_map.get(code, {})
            avg_pct = al.get("avg_pct", 0)
            competency = al.get("competencies", "") or pos

            # Classify by allocation %
            if avg_pct >= 80:   status = "Allocated"
            elif avg_pct >= 20: status = "Partial"
            else:               status = "Bench"

            # Timesheet utilization label
            ts_hours = ts.get("total_hours", 0)
            if ts_hours >= 120:   ts_label = "High Activity"
            elif ts_hours >= 40:  ts_label = "Moderate Activity"
            elif ts_hours > 0:    ts_label = "Low Activity"
            else:                 ts_label = "No Timesheet"

            # Project context: merge allocation projects + timesheet projects
            proj_combined = al.get("proj_list", "") or ts.get("projects_ts", "")

            emp = {
                "name":            name,
                "code":            code,
                "position":        pos,
                "location":        loc,
                "hierarchy":       hier,
                "employee_type":   etype,
                "employee_status": estatus,
                "competency":      competency,
                "avg_allocation":  avg_pct,
                "max_allocation":  al.get("max_pct", 0),
                "proj_count":      al.get("proj_count", 0),
                "proj_list":       proj_combined,
                "flag":            al.get("flag", ""),
                "actual_weeks":    al.get("actual_weeks", 0),
                "forecast_weeks":  al.get("forecast_weeks", 0),
                "last_alloc_date": al.get("last_alloc_date", ""),
                "ts_hours_90d":    ts_hours,
                "ts_label":        ts_label,
                "ts_projects":     ts.get("projects_ts", ""),
                "last_ts_date":    ts.get("last_ts_date", ""),
                "status":          status,
            }

            # Filters
            searchable = f"{name} {competency} {pos} {loc} {hier}".lower()
            if search and search not in searchable:
                continue
            if status_filter and emp["status"] != status_filter:
                continue
            if competency_filter and competency_filter not in competency.lower():
                continue

            employees.append(emp)

        # Sort: Bench → Partial → Allocated, then by name
        order = {"Bench": 0, "Partial": 1, "Allocated": 2}
        employees.sort(key=lambda e: (order.get(e["status"], 3), e["name"]))

        summary = {
            "total":     len(employees),
            "allocated": sum(1 for e in employees if e["status"] == "Allocated"),
            "partial":   sum(1 for e in employees if e["status"] == "Partial"),
            "bench":     sum(1 for e in employees if e["status"] == "Bench"),
            "high_activity": sum(1 for e in employees if e["ts_label"] == "High Activity"),
            "no_timesheet":  sum(1 for e in employees if e["ts_label"] == "No Timesheet"),
        }

        # Competency distribution for the cloud
        comp_freq = {}
        for e in employees:
            for c in (e["competency"] or "").split("|"):
                c = c.strip()
                if c and len(c) > 1:
                    comp_freq[c] = comp_freq.get(c, 0) + 1
        top_competencies = sorted(comp_freq.items(), key=lambda x: -x[1])[:20]

        return jsonify({
            "employees": employees,
            "summary":   summary,
            "top_competencies": [{"skill": k, "count": v} for k, v in top_competencies],
        })

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# =====================================================================
# SKILL ENRICHMENT / SEARCH
# =====================================================================


# =====================================================================
# /api/skill-search
# =====================================================================
@router.get("/api/skill-search")
async def skill_search(req: Request):
    return to_response(_skill_search(FlaskReq(req)))


def _skill_search(request):
    """Search employees by skill/competency.

    Uses exact columns:
      Allocation_data: employee_id, emp_competency, allocation_percent, project_id, Flag, week_id, year_id
      Employee_Data:   Employee_Code, Resource_Name, Employee_Position, Employee_Location,
                       Employee_Status, Employee_Type, Employee_Hierarchy
      Timesheet_Data:  TICKET_USER_ID, TICKET_PROJECT_LABEL, TICKET_HOURS,
                       TICKET_SUBJECT, TICKET_STATUS, DATE_KEY
    """
    settings = load_settings()
    project  = settings["gcp_project"]
    dataset  = settings["bq_dataset"]
    emp_table   = f"`{project}.{dataset}.Employee_Data`"
    alloc_table = f"`{project}.{dataset}.Allocation_data`"
    ts_table    = f"`{project}.{dataset}.Timesheet_Data`"

    query      = request.args.get("q", "").strip().lower()
    avail_only = request.args.get("available_only", "false").lower() == "true"

    try:
        client = get_bq_client(settings)

        # ── Allocation map per employee (exact columns) ─────────────────────
        alloc_sql = f"""
        SELECT
            CAST(employee_id AS STRING)                             AS emp_id,
            ROUND(AVG(SAFE_CAST(allocation_percent AS FLOAT64)), 1) AS avg_pct,
            COUNT(DISTINCT project_id)                              AS proj_count,
            STRING_AGG(DISTINCT CAST(project_id AS STRING), ', ' ORDER BY CAST(project_id AS STRING) LIMIT 8) AS proj_list,
            STRING_AGG(DISTINCT emp_competency, ' | ' ORDER BY emp_competency LIMIT 10) AS all_competencies,
            MAX(Flag)                                               AS flag,
            COUNTIF(Flag = 'Actual')                                AS actual_weeks,
            MAX(CAST(week_id AS STRING))                            AS latest_week,
            MAX(CAST(year_id AS STRING))                            AS latest_year
        FROM {alloc_table}
        GROUP BY employee_id
        """
        alloc_map = {}
        for row in client.query(alloc_sql).result():
            key = _normalize_eid(row.emp_id)
            alloc_map[key] = {
                "avg_pct":       float(row.avg_pct or 0),
                "proj_count":    int(row.proj_count or 0),
                "proj_list":     str(row.proj_list or ""),
                "competencies":  str(row.all_competencies or ""),
                "flag":          str(row.flag or ""),
                "actual_weeks":  int(row.actual_weeks or 0),
                "latest_week":   str(row.latest_week or ""),
                "latest_year":   str(row.latest_year or ""),
            }

        # ── Timesheet enrichment: hours + projects + ticket types ───────────
        # Gives us richer project context per employee via TICKET_PROJECT_LABEL
        ts_sql = f"""
        SELECT
            CAST(TICKET_USER_ID AS STRING)                              AS ts_user,
            ROUND(SUM(SAFE_CAST(TICKET_HOURS AS FLOAT64)), 1)           AS total_hours,
            COUNT(DISTINCT TICKET_ID)                                   AS ticket_count,
            COUNT(DISTINCT TICKET_PROJECT_LABEL)                        AS ts_proj_count,
            STRING_AGG(DISTINCT TICKET_PROJECT_LABEL, ', '
                ORDER BY TICKET_PROJECT_LABEL LIMIT 6)                  AS ts_projects,
            STRING_AGG(DISTINCT TICKET_STATUS, ', '
                ORDER BY TICKET_STATUS LIMIT 5)                         AS ticket_statuses,
            ROUND(AVG(SAFE_CAST(LOG_SCORE AS FLOAT64)), 2)              AS avg_log_score,
            MAX(CAST(DATE_KEY AS STRING))                               AS last_ts_date
        FROM {ts_table}
        WHERE DATE_KEY IS NOT NULL
          AND DATE(DATE_KEY) >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
          AND TICKET_HOURS IS NOT NULL
        GROUP BY TICKET_USER_ID
        """
        ts_map = {}
        for row in client.query(ts_sql).result():
            key = _normalize_eid(row.ts_user)
            ts_map[key] = {
                "total_hours":    float(row.total_hours or 0),
                "ticket_count":   int(row.ticket_count or 0),
                "ts_proj_count":  int(row.ts_proj_count or 0),
                "ts_projects":    str(row.ts_projects or ""),
                "ticket_statuses":str(row.ticket_statuses or ""),
                "avg_log_score":  float(row.avg_log_score or 0),
                "last_ts_date":   str(row.last_ts_date or "")[:10],
            }

        # ── Global competency cloud (for display even with no query) ────────
        all_competencies = {}
        for al in alloc_map.values():
            for c in (al.get("competencies") or "").split("|"):
                c = c.strip()
                if c and len(c) > 1:
                    all_competencies[c] = all_competencies.get(c, 0) + 1

        # ── Employee master (exact columns) ─────────────────────────────────
        emp_sql = f"""
        SELECT
            Employee_Code, Resource_Name, Employee_Position,
            Employee_Location, Employee_Status, Employee_Type,
            Employee_Hierarchy, Employee_Email
        FROM {emp_table}
        WHERE LOWER(COALESCE(Employee_Type,'')) IN ('mto','permanent','probation')
        """

        results = []
        for row in client.query(emp_sql).result():
            name  = str(row.Resource_Name    or "").strip()
            code  = _normalize_eid(row.Employee_Code or "")
            pos   = str(row.Employee_Position or "").strip()
            loc   = str(row.Employee_Location or "").strip()
            hier  = str(row.Employee_Hierarchy or "").strip()
            etype = str(row.Employee_Type or "").strip()

            al = alloc_map.get(code, {})
            ts = ts_map.get(code, {})

            competencies = al.get("competencies", "") or pos
            avg_pct = al.get("avg_pct", 0)

            # Project context: prefer timesheet project labels (more descriptive)
            proj_context = ts.get("ts_projects", "") or al.get("proj_list", "")

            # Availability
            if avg_pct >= 80:   avail_status = "Allocated"
            elif avg_pct >= 20: avail_status = "Partial"
            else:               avail_status = "Bench"

            # Productivity score from timesheet (0-100)
            ts_hours = ts.get("total_hours", 0)
            log_score = ts.get("avg_log_score", 0)
            if ts_hours > 0:
                productivity = min(100, round((ts_hours / 480) * 50 + (log_score * 50), 0))
            else:
                productivity = 0

            # Skill match filter
            searchable = f"{name} {competencies} {pos} {loc} {hier} {proj_context}".lower()
            if query and query not in searchable:
                continue
            if avail_only and avail_status == "Allocated":
                continue

            results.append({
                "name":           name,
                "code":           code,
                "position":       pos,
                "location":       loc,
                "hierarchy":      hier,
                "employee_type":  etype,
                "competencies":   competencies,
                "avg_allocation": avg_pct,
                "proj_count":     al.get("proj_count", 0),
                "proj_list":      proj_context,
                "flag":           al.get("flag", ""),
                "actual_weeks":   al.get("actual_weeks", 0),
                "ts_hours_90d":   ts_hours,
                "ticket_count":   ts.get("ticket_count", 0),
                "ts_projects":    ts.get("ts_projects", ""),
                "avg_log_score":  log_score,
                "productivity":   int(productivity),
                "last_ts_date":   ts.get("last_ts_date", ""),
                "availability":   avail_status,
            })

        results.sort(key=lambda e: (
            0 if e["availability"] == "Bench" else 1 if e["availability"] == "Partial" else 2,
            e["name"]
        ))

        top_skills = sorted(all_competencies.items(), key=lambda x: -x[1])[:30]

        return jsonify({
            "results":    results,
            "total":      len(results),
            "top_skills": [{"skill": k, "count": v} for k, v in top_skills],
        })

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# =====================================================================
# PREDICTIVE ATTENDANCE PATTERNS
# =====================================================================

# =====================================================================
# /api/predictive-attendance
# =====================================================================
@router.get("/api/predictive-attendance")
async def predictive_attendance(req: Request):
    return to_response(_predictive_attendance(FlaskReq(req)))


def _predictive_attendance(request):
    """Detect declining attendance trends and surface at-risk employees."""
    settings = load_settings()
    project  = settings["gcp_project"]
    dataset  = settings["bq_dataset"]
    att_table = f"`{project}.{dataset}.Attendance_Data`"
    weeks_back = int(request.args.get("weeks", 8))

    try:
        client = get_bq_client(settings)

        # ── Query 1: per-employee summary stats (no STRUCT, no ARRAY_AGG) ──
        summary_sql = f"""
        WITH weekly AS (
            SELECT
                employee_name,
                DATE_TRUNC(attendance_date, WEEK) AS week_start,
                ROUND(100.0 * SUM(is_present) / NULLIF(COUNT(*),0), 1) AS week_rate,
                COUNT(*) AS total_days,
                SUM(is_absent) AS absent_days,
                SUM(CASE WHEN is_present=1
                    AND SAFE_CAST(checkin_time AS TIMESTAMP) IS NOT NULL
                    AND EXTRACT(TIME FROM SAFE_CAST(checkin_time AS TIMESTAMP)) > TIME(9,30,0)
                    THEN 1 ELSE 0 END) AS late_days
            FROM {att_table}
            WHERE attendance_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {weeks_back} WEEK)
              AND is_holiday = 0 AND is_weekend = 0
              AND employee_name IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT
            employee_name,
            COUNT(week_start)  AS weeks_recorded,
            ROUND(AVG(week_rate), 1) AS overall_rate,
            ROUND(AVG(CASE WHEN week_start >= DATE_SUB(CURRENT_DATE(), INTERVAL 4 WEEK)
                THEN week_rate END), 1) AS recent_4w_rate,
            ROUND(AVG(CASE WHEN week_start < DATE_SUB(CURRENT_DATE(), INTERVAL 4 WEEK)
                THEN week_rate END), 1) AS prev_4w_rate,
            SUM(absent_days)   AS total_absent,
            SUM(late_days)     AS total_late,
            SUM(total_days)    AS total_working_days
        FROM weekly
        GROUP BY employee_name
        HAVING COUNT(week_start) >= 2
        ORDER BY (COALESCE(AVG(CASE WHEN week_start >= DATE_SUB(CURRENT_DATE(), INTERVAL 4 WEEK) THEN week_rate END),0)
                 - COALESCE(AVG(CASE WHEN week_start < DATE_SUB(CURRENT_DATE(), INTERVAL 4 WEEK) THEN week_rate END),0)) ASC
        LIMIT 200
        """

        # ── Query 2: flat weekly rows (safe — no STRUCT) ──
        weekly_sql = f"""
        SELECT
            employee_name,
            CAST(DATE_TRUNC(attendance_date, WEEK) AS STRING) AS week_start,
            ROUND(100.0 * SUM(is_present) / NULLIF(COUNT(*),0), 1) AS week_rate
        FROM {att_table}
        WHERE attendance_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {weeks_back} WEEK)
          AND is_holiday = 0 AND is_weekend = 0
          AND employee_name IS NOT NULL
        GROUP BY 1, 2
        ORDER BY 1, 2
        """

        summary_rows = list(client.query(summary_sql).result())
        weekly_rows  = list(client.query(weekly_sql).result())

        # Group weekly data by employee in Python
        from collections import defaultdict
        sparklines = defaultdict(list)
        for r in weekly_rows:
            sparklines[r.employee_name].append({"week": r.week_start, "rate": float(r.week_rate or 0)})

        employees = []
        for row in summary_rows:
            recent  = float(row.recent_4w_rate  or 0)
            prev    = float(row.prev_4w_rate    or 0)
            overall = float(row.overall_rate    or 0)
            delta   = round(recent - prev, 1) if row.prev_4w_rate is not None else 0

            # Risk scoring
            if overall < 60 or (delta <= -20 and recent < 70):
                risk = "High"
            elif overall < 75 or delta <= -10:
                risk = "Medium"
            else:
                risk = "Low"

            employees.append({
                "name":           row.employee_name,
                "overall_rate":   overall,
                "recent_4w_rate": recent,
                "prev_4w_rate":   prev,
                "delta":          delta,
                "total_absent":   int(row.total_absent or 0),
                "total_late":     int(row.total_late or 0),
                "total_days":     int(row.total_working_days or 0),
                "weeks_recorded": int(row.weeks_recorded or 0),
                "risk":           risk,
                "sparkline":      sparklines.get(row.employee_name, []),
            })

        # Sort: High risk first, then by lowest attendance
        employees.sort(key=lambda e: (0 if e["risk"]=="High" else 1 if e["risk"]=="Medium" else 2, e["overall_rate"]))

        summary = {
            "total":      len(employees),
            "high_risk":  sum(1 for e in employees if e["risk"] == "High"),
            "medium_risk":sum(1 for e in employees if e["risk"] == "Medium"),
            "low_risk":   sum(1 for e in employees if e["risk"] == "Low"),
            "avg_rate":   round(sum(e["overall_rate"] for e in employees) / len(employees), 1) if employees else 0,
        }

        # ── Query 3: org-level weekly trend ──
        org_trend_sql = f"""
        SELECT
            CAST(DATE_TRUNC(attendance_date, WEEK) AS STRING) AS week_start,
            ROUND(100.0 * SUM(is_present) / NULLIF(COUNT(*),0), 1) AS rate
        FROM {att_table}
        WHERE attendance_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {weeks_back} WEEK)
          AND is_holiday=0 AND is_weekend=0
        GROUP BY 1 ORDER BY 1
        """
        org_trend = [{"week": r.week_start, "rate": float(r.rate or 0)}
                     for r in client.query(org_trend_sql).result()]

        return jsonify({"employees": employees, "summary": summary, "org_trend": org_trend})

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# =====================================================================
# CAPABILITY INTELLIGENCE MATRIX


# =====================================================================
# /api/capability-matrix
# =====================================================================
@router.get("/api/capability-matrix")
async def capability_matrix(req: Request):
    return to_response(_capability_matrix(FlaskReq(req)))


def _capability_matrix(request):
    """
    Capability Intelligence Matrix — active employees only.
    Scores across 2 categories (Attendance Reliability + Project Engagement).
    Avoids ALL type-sensitive operations on checkin_time/checkout_time.
    Joins:
      Employee_Data.Resource_Name = Attendance_Data.employee_name
      Employee_Data.Employee_Code = Allocation_data.employee_id  (normalised)
      Employee_Data.Employee_Code = Timesheet_Data.TICKET_USER_ID (normalised)
    """
    settings   = load_settings()
    project    = settings.get("gcp_project", "")
    dataset    = settings.get("bq_dataset", "")
    categories = settings.get("matrix_categories", [])[:2]

    if not project or not dataset:
        return jsonify({"error": "GCP project / dataset not configured in Settings"}), 400

    att_table   = f"`{project}.{dataset}.Attendance_Data`"
    alloc_table = f"`{project}.{dataset}.Allocation_data`"
    ts_table    = f"`{project}.{dataset}.Timesheet_Data`"
    emp_table   = f"`{project}.{dataset}.Employee_Data`"

    def to_stars(score):
        return round(min(max(float(score or 0), 0), 100) / 20 * 2) / 2

    try:
        client = get_bq_client(settings)

        # ── 1. Attendance — SAFE_CAST to BOOL handles both INT64 and BOOL columns
        #    Deliberately avoids checkin_time / checkout_time entirely
        att_sql = f"""
        SELECT
            employee_name,
            COUNT(*) AS total_days,
            COUNTIF(SAFE_CAST(is_present  AS BOOL) = TRUE) AS present_days,
            COUNTIF(SAFE_CAST(is_absent   AS BOOL) = TRUE) AS absent_days,
            COUNTIF(SAFE_CAST(is_on_leave AS BOOL) = TRUE) AS leave_days,
            COUNTIF(SAFE_CAST(is_remote   AS BOOL) = TRUE) AS remote_days,
            ROUND(
                100.0 * COUNTIF(SAFE_CAST(is_present AS BOOL) = TRUE)
                / NULLIF(COUNT(*), 0), 1
            ) AS presence_rate
        FROM {att_table}
        WHERE SAFE_CAST(is_holiday AS BOOL) != TRUE
          AND SAFE_CAST(is_weekend AS BOOL) != TRUE
          AND employee_name IS NOT NULL
        GROUP BY employee_name
        """

        # ── 2. Allocation — straightforward aggregates, no date columns used
        alloc_sql = f"""
        SELECT
            CAST(employee_id AS STRING)                                AS emp_id,
            ROUND(AVG(SAFE_CAST(allocation_percent AS FLOAT64)), 1)    AS avg_alloc,
            COUNT(DISTINCT project_id)                                 AS project_count,
            STRING_AGG(DISTINCT CAST(project_id AS STRING), ', '
                ORDER BY CAST(project_id AS STRING) LIMIT 10)         AS project_list,
            STRING_AGG(DISTINCT emp_competency, ' | '
                ORDER BY emp_competency LIMIT 15)                     AS competencies,
            COUNTIF(Flag = 'Actual')                                   AS actual_weeks
        FROM {alloc_table}
        GROUP BY employee_id
        """

        # ── 3. Timesheet last 90 days — DATE(DATE_KEY) avoids string/datetime cast issues
        ts_sql = f"""
        SELECT
            CAST(TICKET_USER_ID AS STRING)                            AS ts_user,
            ROUND(SUM(SAFE_CAST(TICKET_HOURS AS FLOAT64)), 1)         AS total_hours,
            COUNT(DISTINCT TICKET_ID)                                 AS ticket_count,
            COUNT(DISTINCT TICKET_PROJECT_LABEL)                      AS ts_proj_count,
            STRING_AGG(DISTINCT TICKET_PROJECT_LABEL, ', '
                ORDER BY TICKET_PROJECT_LABEL LIMIT 8)               AS ts_projects,
            ROUND(AVG(SAFE_CAST(LOG_SCORE AS FLOAT64)), 3)            AS avg_log_score
        FROM {ts_table}
        WHERE DATE_KEY IS NOT NULL
          AND DATE(DATE_KEY) >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
          AND TICKET_HOURS IS NOT NULL
        GROUP BY TICKET_USER_ID
        """

        # ── 4. Active employees only (targets ~1,140 active headcount)
        emp_sql = f"""
        SELECT
            Employee_Code, Resource_Name, Employee_Position,
            Employee_Location, Employee_Status, Employee_Type,
            Employee_Hierarchy, Employee_Email
        FROM {emp_table}
        WHERE LOWER(COALESCE(Employee_Status, '')) = 'active'
          AND LOWER(COALESCE(Employee_Type, '')) IN ('mto', 'permanent', 'probation')
        """

        # ── Execute all queries
        att_rows   = list(client.query(att_sql).result())
        alloc_rows = list(client.query(alloc_sql).result())
        ts_rows    = list(client.query(ts_sql).result())
        emp_rows   = list(client.query(emp_sql).result())

        # Index by key
        att_by_name = {r.employee_name: r for r in att_rows}

        alloc_by_eid = {}
        for r in alloc_rows:
            key = _normalize_eid(str(r.emp_id or ""))
            alloc_by_eid[key] = {
                "avg_alloc":     float(r.avg_alloc    or 0),
                "project_count": int(r.project_count  or 0),
                "project_list":  str(r.project_list   or ""),
                "competencies":  str(r.competencies   or ""),
                "actual_weeks":  int(r.actual_weeks   or 0),
            }

        ts_by_uid = {}
        for r in ts_rows:
            key = _normalize_eid(str(r.ts_user or ""))
            ts_by_uid[key] = {
                "total_hours":   float(r.total_hours   or 0),
                "ticket_count":  int(r.ticket_count    or 0),
                "ts_proj_count": int(r.ts_proj_count   or 0),
                "ts_projects":   str(r.ts_projects     or ""),
                "avg_log_score": float(r.avg_log_score or 0),
            }

        # ── Build records
        employees        = []
        all_competencies = {}

        for row in emp_rows:
            name  = str(row.Resource_Name     or "").strip()
            code  = _normalize_eid(str(row.Employee_Code or ""))
            pos   = str(row.Employee_Position  or "").strip()
            loc   = str(row.Employee_Location  or "").strip()
            hier  = str(row.Employee_Hierarchy or "").strip()
            etype = str(row.Employee_Type      or "").strip()
            email = str(row.Employee_Email     or "").strip()
            if not name:
                continue

            att = att_by_name.get(name)
            al  = alloc_by_eid.get(code, {})
            ts  = ts_by_uid.get(code, {})

            total_days    = int(att.total_days    or 0) if att else 0
            present_days  = int(att.present_days  or 0) if att else 0
            absent_days   = int(att.absent_days   or 0) if att else 0
            leave_days    = int(att.leave_days    or 0) if att else 0
            remote_days   = int(att.remote_days   or 0) if att else 0
            presence_rate = float(att.presence_rate or 0) if att else 0.0

            avg_alloc     = al.get("avg_alloc", 0.0)
            project_count = al.get("project_count", 0)
            project_list  = al.get("project_list", "")
            competencies  = al.get("competencies", "") or pos
            actual_weeks  = al.get("actual_weeks", 0)

            ts_hours      = ts.get("total_hours", 0.0)
            ticket_count  = ts.get("ticket_count", 0)
            ts_proj_count = ts.get("ts_proj_count", 0)
            ts_projects   = ts.get("ts_projects", "") or project_list
            log_score     = ts.get("avg_log_score", 0.0)

            for c in competencies.split("|"):
                c = c.strip()
                if c and len(c) > 1:
                    all_competencies[c] = all_competencies.get(c, 0) + 1

            # ── Scores (0–100)
            # Reliability  = raw attendance presence rate (already 0–100)
            reliability_score = round(presence_rate, 1)

            # Engagement   = 60% avg allocation + 40% project breadth (max 5 projects)
            engagement_score = round(
                min(avg_alloc, 100) * 0.6 +
                min(project_count / 5.0, 1.0) * 100 * 0.4, 1
            )

            score_map = {
                "reliability": reliability_score,
                "engagement":  engagement_score,
            }

            star_ratings = {cat["id"]: to_stars(score_map.get(cat["id"], 0)) for cat in categories}

            if avg_alloc >= 80:   alloc_status = "Allocated"
            elif avg_alloc >= 20: alloc_status = "Partial"
            else:                 alloc_status = "Bench"

            log_label = ("Excellent" if log_score >= 0.9 else
                         "Good"      if log_score >= 0.7 else
                         "Fair"      if log_score >= 0.5 else "Poor")

            employees.append({
                "name": name, "code": code, "position": pos,
                "location": loc, "hierarchy": hier,
                "employee_type": etype, "email": email,
                "competencies": competencies,
                "alloc_status": alloc_status,
                "avg_alloc": round(avg_alloc, 1),
                "project_count": project_count,
                "project_list": project_list,
                "ts_projects": ts_projects,
                "actual_weeks": actual_weeks,
                "total_days": total_days,
                "present_days": present_days,
                "absent_days": absent_days,
                "leave_days": leave_days,
                "remote_days": remote_days,
                "presence_rate": presence_rate,
                "ts_hours_90d": ts_hours,
                "ticket_count": ticket_count,
                "ts_proj_count": ts_proj_count,
                "avg_log_score": round(log_score, 3),
                "log_label": log_label,
                "scores": score_map,
                "stars": star_ratings,
            })

        employees.sort(
            key=lambda e: sum(e["stars"].values()) / max(len(e["stars"]), 1),
            reverse=True
        )

        total   = len(employees)
        summary = {"total": total}
        for cat in categories:
            cid = cat["id"]
            vals = [e["stars"].get(cid, 0) for e in employees]
            summary[f"avg_stars_{cid}"] = round(sum(vals) / len(vals), 2) if vals else 0.0

        top_skills = sorted(all_competencies.items(), key=lambda x: -x[1])[:40]

        return jsonify({
            "employees":  employees,
            "total":      total,
            "summary":    summary,
            "categories": categories,
            "top_skills": [{"skill": k, "count": v} for k, v in top_skills],
        })

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# =====================================================================
# AVAILABILITY ENGINE — DEPARTMENTS + AI STAFFING SUGGESTIONS
# =====================================================================


# =====================================================================
# /api/departments
# =====================================================================
@router.get("/api/departments")
async def get_departments(req: Request):
    return to_response(_get_departments(FlaskReq(req)))


def _get_departments(request):
    """Return distinct departments / hierarchies for the staffing modal."""
    settings = load_settings()
    project = settings["gcp_project"]
    dataset = settings["bq_dataset"]
    try:
        client = get_bq_client(settings)
        sql = f"""
        SELECT
            COALESCE(NULLIF(TRIM(Employee_Hierarchy), ''), 'Unspecified') AS dept,
            COUNT(*) AS headcount
        FROM `{project}.{dataset}.Employee_Data`
        WHERE LOWER(COALESCE(Employee_Type,'')) IN ('mto','permanent','probation')
        GROUP BY dept
        ORDER BY headcount DESC
        """
        depts = [{"name": r.dept, "headcount": int(r.headcount or 0)} for r in client.query(sql).result()]
        return jsonify({"departments": depts})
    except Exception as e:
        return jsonify({"error": str(e), "departments": []}), 500




# =====================================================================
# /api/ai-staff-suggestions
# =====================================================================
@router.post("/api/ai-staff-suggestions")
async def ai_staff_suggestions(req: Request):
    body = await adapt_body(req)
    return to_response(_ai_staff_suggestions(FlaskReq(req, body)))


def _ai_staff_suggestions(request):
    """
    Given a project brief + a department, return AI-ranked best-fit employees.
    Body: { project_name, project_description, department, skills_required (string) }
    """
    payload = request.get_json(force=True) or {}
    project_name        = (payload.get("project_name") or "").strip()
    project_description = (payload.get("project_description") or "").strip()
    department          = (payload.get("department") or "").strip()
    skills_required     = (payload.get("skills_required") or "").strip()

    if not project_name or not department:
        return jsonify({"error": "project_name and department are required"}), 400

    settings = load_settings()
    project  = settings["gcp_project"]
    dataset  = settings["bq_dataset"]

    try:
        client = get_bq_client(settings)

        # Pull candidates from the chosen department + their allocation summary
        cand_sql = f"""
        WITH alloc AS (
            SELECT
                CAST(employee_id AS STRING) AS emp_id,
                ROUND(AVG(SAFE_CAST(allocation_percent AS FLOAT64)), 1) AS avg_pct,
                COUNT(DISTINCT project_id) AS proj_count,
                STRING_AGG(DISTINCT emp_competency, ' | ' ORDER BY emp_competency LIMIT 6) AS competencies
            FROM `{project}.{dataset}.Allocation_data`
            GROUP BY employee_id
        ),
        ts AS (
            SELECT
                CAST(TICKET_USER_ID AS STRING) AS ts_user,
                ROUND(SUM(SAFE_CAST(TICKET_HOURS AS FLOAT64)), 1) AS ts_hours_90d
            FROM `{project}.{dataset}.Timesheet_Data`
            WHERE DATE_KEY IS NOT NULL
              AND DATE(DATE_KEY) >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
            GROUP BY TICKET_USER_ID
        )
        SELECT
            e.Resource_Name AS name,
            e.Employee_Code AS code,
            e.Employee_Position AS position,
            e.Employee_Location AS location,
            e.Employee_Hierarchy AS department,
            COALESCE(a.avg_pct, 0) AS avg_pct,
            COALESCE(a.proj_count, 0) AS proj_count,
            COALESCE(a.competencies, '') AS competencies,
            COALESCE(t.ts_hours_90d, 0) AS ts_hours_90d
        FROM `{project}.{dataset}.Employee_Data` e
        LEFT JOIN alloc a ON REGEXP_REPLACE(CAST(e.Employee_Code AS STRING), r'^[A-Za-z]+-?0*', '') =
                             REGEXP_REPLACE(a.emp_id,                     r'^[A-Za-z]+-?0*', '')
        LEFT JOIN ts    t ON REGEXP_REPLACE(CAST(e.Employee_Code AS STRING), r'^[A-Za-z]+-?0*', '') =
                             REGEXP_REPLACE(t.ts_user,                    r'^[A-Za-z]+-?0*', '')
        WHERE LOWER(COALESCE(e.Employee_Type,'')) IN ('mto','permanent','probation')
          AND LOWER(COALESCE(e.Employee_Hierarchy,'')) = LOWER(@dept)
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("dept", "STRING", department)]
        )
        candidates = []
        for r in client.query(cand_sql, job_config=job_config).result():
            avg_pct  = float(r.avg_pct or 0)
            free_pct = max(0, 100 - avg_pct)
            candidates.append({
                "name":          r.name,
                "code":          r.code,
                "position":      r.position or "",
                "location":      r.location or "",
                "department":    r.department or "",
                "avg_allocation": avg_pct,
                "free_capacity": round(free_pct, 1),
                "active_projects": int(r.proj_count or 0),
                "competencies":  r.competencies or "",
                "ts_hours_90d":  float(r.ts_hours_90d or 0),
            })

        if not candidates:
            return jsonify({
                "suggestions": [],
                "message": f"No active employees found in '{department}'."
            })

        # Build a compact roster for the model
        roster = [
            {
                "id":       i,
                "name":     c["name"],
                "position": c["position"],
                "skills":   c["competencies"],
                "current_allocation_pct": c["avg_allocation"],
                "free_capacity_pct":      c["free_capacity"],
                "active_projects":        c["active_projects"],
                "ts_hours_last_90d":      c["ts_hours_90d"],
                "location":               c["location"],
            }
            for i, c in enumerate(candidates)
        ]

        prompt = f"""You are Satori, TMC's AI staffing advisor. A manager needs to staff a NEW project.

PROJECT:
- Name: {project_name}
- Description: {project_description or "(not provided)"}
- Skills required: {skills_required or "(open)"}
- Department: {department}

CANDIDATE ROSTER (JSON):
{json.dumps(roster, indent=2)}

TASK:
Pick the TOP 5 BEST FIT employees from the roster. Rank them 1 (best) → 5.
Scoring guidance:
  • Free capacity (higher = better — they can actually take new work).
  • Skill match against the required skills / position.
  • Healthy project count (1-3 active projects = focused; 0 = bench but might be rusty; 4+ = over-stretched).
  • Recent timesheet activity (engaged but not burnt out).

Return STRICT JSON ONLY in this exact shape (no markdown, no commentary):
{{
  "suggestions": [
    {{
      "id": <roster id>,
      "fit_score": <0-100 integer>,
      "headline": "<one-line strength, max 80 chars>",
      "reasoning": "<2-3 sentences explaining why they fit>",
      "caveat": "<optional risk/concern, or empty string>"
    }}
  ]
}}
Return at most 5 items, ordered best→worst.
"""

        try:
            client_genai = get_genai_client(settings)
            resp = client_genai.models.generate_content(
                model=settings.get("gemini_model", "gemini-2.5-flash"),
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.3,
                ),
            )
            raw = (resp.text or "").strip()
            ai_data = json.loads(raw) if raw else {"suggestions": []}
        except Exception as ge:
            # Fallback: deterministic ranking by free capacity + skill keyword match
            kw = [k.strip().lower() for k in skills_required.replace(",", " ").split() if k.strip()]
            def score(c):
                s = c["free_capacity"]
                comp = (c["competencies"] or "").lower()
                pos  = (c["position"] or "").lower()
                hits = sum(1 for k in kw if k in comp or k in pos)
                return s + hits * 15
            ranked = sorted(enumerate(candidates), key=lambda x: -score(x[1]))[:5]
            ai_data = {
                "suggestions": [
                    {
                        "id": idx,
                        "fit_score": int(min(100, score(c))),
                        "headline": f"{int(c['free_capacity'])}% free capacity",
                        "reasoning": f"{c['name']} has {int(c['free_capacity'])}% free capacity across {c['active_projects']} active project(s). "
                                     f"Skills on record: {c['competencies'] or c['position'] or 'n/a'}.",
                        "caveat": "AI ranking unavailable — fallback heuristic shown." if ge else "",
                    }
                    for idx, c in ranked
                ],
                "ai_error": str(ge),
            }

        # Hydrate suggestions with full candidate detail
        hydrated = []
        for s in ai_data.get("suggestions", [])[:5]:
            try:
                idx = int(s.get("id"))
                if 0 <= idx < len(candidates):
                    c = candidates[idx]
                    hydrated.append({
                        **c,
                        "fit_score": int(s.get("fit_score") or 0),
                        "headline":  s.get("headline") or "",
                        "reasoning": s.get("reasoning") or "",
                        "caveat":    s.get("caveat") or "",
                    })
            except Exception:
                continue

        return jsonify({
            "suggestions":     hydrated,
            "total_candidates": len(candidates),
            "department":       department,
            "project_name":     project_name,
        })

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# =====================================================================
# PREDICTIVE PATTERNS — PER-EMPLOYEE DETAIL + AI TIPS
# =====================================================================


# =====================================================================
# /api/employee-pattern
# =====================================================================
@router.get("/api/employee-pattern")
async def employee_pattern(req: Request):
    return to_response(_employee_pattern(FlaskReq(req)))


def _employee_pattern(request):
    """Detailed attendance pattern for a single employee, with predictions."""
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name parameter required"}), 400

    weeks_back = int(request.args.get("weeks", 16))
    settings = load_settings()
    project  = settings["gcp_project"]
    dataset  = settings["bq_dataset"]
    att      = f"`{project}.{dataset}.Attendance_Data`"

    try:
        client = get_bq_client(settings)
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("name", "STRING", name)]
        )

        # Overall stats + weekday breakdown + monthly trend (single round-trip per section)
        weekday_sql = f"""
        SELECT
            FORMAT_DATE('%A', attendance_date) AS weekday,
            EXTRACT(DAYOFWEEK FROM attendance_date) AS dow,
            COUNT(*) AS total_days,
            SUM(is_present) AS present_days,
            SUM(is_absent)  AS absent_days,
            SUM(CASE WHEN is_present=1
                AND SAFE_CAST(checkin_time AS TIMESTAMP) IS NOT NULL
                AND EXTRACT(TIME FROM SAFE_CAST(checkin_time AS TIMESTAMP)) > TIME(9,30,0)
                THEN 1 ELSE 0 END) AS late_days
        FROM {att}
        WHERE employee_name = @name
          AND is_holiday = 0 AND is_weekend = 0
          AND attendance_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {weeks_back} WEEK)
        GROUP BY weekday, dow
        ORDER BY dow
        """

        weekly_sql = f"""
        SELECT
            CAST(DATE_TRUNC(attendance_date, WEEK) AS STRING) AS week_start,
            ROUND(100.0 * SUM(is_present) / NULLIF(COUNT(*),0), 1) AS week_rate,
            SUM(is_absent) AS absences,
            SUM(CASE WHEN is_present=1
                AND SAFE_CAST(checkin_time AS TIMESTAMP) IS NOT NULL
                AND EXTRACT(TIME FROM SAFE_CAST(checkin_time AS TIMESTAMP)) > TIME(9,30,0)
                THEN 1 ELSE 0 END) AS lates
        FROM {att}
        WHERE employee_name = @name
          AND is_holiday = 0 AND is_weekend = 0
          AND attendance_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {weeks_back} WEEK)
        GROUP BY 1 ORDER BY 1
        """

        recent_absences_sql = f"""
        SELECT CAST(attendance_date AS STRING) AS d, attendance_status_text AS status
        FROM {att}
        WHERE employee_name = @name
          AND is_holiday = 0 AND is_weekend = 0
          AND is_absent = 1
          AND attendance_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {weeks_back} WEEK)
        ORDER BY attendance_date DESC
        LIMIT 10
        """

        late_times_sql = f"""
        SELECT
            ROUND(AVG(EXTRACT(HOUR FROM SAFE_CAST(checkin_time AS TIMESTAMP)) * 60
                    + EXTRACT(MINUTE FROM SAFE_CAST(checkin_time AS TIMESTAMP))), 0) AS avg_checkin_min,
            ROUND(STDDEV(EXTRACT(HOUR FROM SAFE_CAST(checkin_time AS TIMESTAMP)) * 60
                       + EXTRACT(MINUTE FROM SAFE_CAST(checkin_time AS TIMESTAMP))), 0) AS std_checkin_min
        FROM {att}
        WHERE employee_name = @name
          AND is_present = 1
          AND SAFE_CAST(checkin_time AS TIMESTAMP) IS NOT NULL
          AND attendance_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {weeks_back} WEEK)
        """

        weekday_rows = list(client.query(weekday_sql, job_config=job_config).result())
        weekly_rows  = list(client.query(weekly_sql,  job_config=bigquery.QueryJobConfig(
                                query_parameters=[bigquery.ScalarQueryParameter("name", "STRING", name)])).result())
        recent_rows  = list(client.query(recent_absences_sql, job_config=bigquery.QueryJobConfig(
                                query_parameters=[bigquery.ScalarQueryParameter("name", "STRING", name)])).result())
        late_rows    = list(client.query(late_times_sql, job_config=bigquery.QueryJobConfig(
                                query_parameters=[bigquery.ScalarQueryParameter("name", "STRING", name)])).result())

        # Weekday pattern
        weekdays = []
        worst_weekday = None
        for r in weekday_rows:
            total = int(r.total_days or 0)
            present = int(r.present_days or 0)
            absent = int(r.absent_days or 0)
            late = int(r.late_days or 0)
            rate = round(100 * present / total, 1) if total else 0
            row = {
                "weekday":   r.weekday,
                "total":     total,
                "present":   present,
                "absent":    absent,
                "late":      late,
                "rate":      rate,
                "late_pct":  round(100 * late / total, 1) if total else 0,
            }
            weekdays.append(row)
            if total >= 3 and (worst_weekday is None or row["rate"] < worst_weekday["rate"]):
                worst_weekday = row

        # Weekly trend
        weekly = []
        for r in weekly_rows:
            weekly.append({
                "week":     r.week_start,
                "rate":     float(r.week_rate or 0),
                "absences": int(r.absences or 0),
                "lates":    int(r.lates or 0),
            })

        # Predictions — last 4-week vs prior 4-week
        recent4 = weekly[-4:]
        prev4   = weekly[-8:-4] if len(weekly) >= 8 else []
        recent_avg = round(sum(w["rate"] for w in recent4) / len(recent4), 1) if recent4 else 0
        prev_avg   = round(sum(w["rate"] for w in prev4)   / len(prev4),   1) if prev4   else recent_avg
        delta      = round(recent_avg - prev_avg, 1)

        # Naive linear projection for next week
        if len(weekly) >= 3:
            xs = list(range(len(weekly[-6:])))
            ys = [w["rate"] for w in weekly[-6:]]
            n  = len(xs)
            mean_x = sum(xs)/n; mean_y = sum(ys)/n
            denom = sum((x-mean_x)**2 for x in xs)
            slope = sum((xs[i]-mean_x)*(ys[i]-mean_y) for i in range(n)) / denom if denom else 0
            intercept = mean_y - slope*mean_x
            projected = max(0, min(100, round(slope*n + intercept, 1)))
        else:
            projected = recent_avg

        # Risk classification
        if recent_avg < 60 or (delta <= -20 and recent_avg < 70):
            risk = "High"
        elif recent_avg < 75 or delta <= -10:
            risk = "Medium"
        else:
            risk = "Low"

        # Lateness summary
        avg_checkin_min = int(late_rows[0].avg_checkin_min or 0) if late_rows and late_rows[0].avg_checkin_min is not None else 0
        std_checkin_min = int(late_rows[0].std_checkin_min or 0) if late_rows and late_rows[0].std_checkin_min is not None else 0
        avg_hh = avg_checkin_min // 60
        avg_mm = avg_checkin_min % 60
        avg_checkin_str = f"{avg_hh:02d}:{avg_mm:02d}" if avg_checkin_min else "—"

        total_late = sum(w["lates"] for w in weekly)
        total_absent = sum(w["absences"] for w in weekly)
        total_present_days = sum(d["present"] for d in weekdays)
        total_days_tracked = sum(d["total"] for d in weekdays)
        overall_rate = round(100 * total_present_days / total_days_tracked, 1) if total_days_tracked else 0

        # Identify problematic patterns
        problems = []
        if worst_weekday and worst_weekday["rate"] < 70:
            problems.append(f"Frequently absent on {worst_weekday['weekday']}s ({worst_weekday['rate']}% present)")
        if avg_checkin_min and avg_checkin_min > 9*60 + 30:
            problems.append(f"Typically arrives at {avg_checkin_str} — past the 9:30 AM cutoff")
        if std_checkin_min and std_checkin_min > 45:
            problems.append(f"Highly irregular check-in times (±{std_checkin_min} min spread)")
        if delta <= -10:
            problems.append(f"Attendance has dropped {abs(delta)} points in the last 4 weeks")
        if total_late >= 5:
            problems.append(f"Late {total_late} times in the last {weeks_back} weeks")

        return jsonify({
            "name":            name,
            "weeks_tracked":   weeks_back,
            "overall_rate":    overall_rate,
            "recent_4w_rate":  recent_avg,
            "prev_4w_rate":    prev_avg,
            "delta":           delta,
            "projected_next_week": projected,
            "risk":            risk,
            "total_absent":    total_absent,
            "total_late":      total_late,
            "avg_checkin":     avg_checkin_str,
            "checkin_std_min": std_checkin_min,
            "weekdays":        weekdays,
            "worst_weekday":   worst_weekday["weekday"] if worst_weekday else None,
            "weekly":          weekly,
            "recent_absences": [{"date": r.d, "status": r.status or "Absent"} for r in recent_rows],
            "problems":        problems,
        })

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500



# =====================================================================
# /api/ai-productivity-tip
# =====================================================================
@router.post("/api/ai-productivity-tip")
async def ai_productivity_tip(req: Request):
    body = await adapt_body(req)
    return to_response(_ai_productivity_tip(FlaskReq(req, body)))


def _ai_productivity_tip(request):
    """Generate an AI productivity tip for an employee from their pattern data."""
    payload = request.get_json(force=True) or {}
    name = (payload.get("name") or "").strip()
    pattern = payload.get("pattern") or {}
    if not name:
        return jsonify({"error": "name required"}), 400

    settings = load_settings()
    try:
        client_genai = get_genai_client(settings)
        prompt = f"""You are Satori, TMC's HR productivity advisor. Read this employee's attendance pattern and
write a SHORT, kind, actionable improvement plan.

EMPLOYEE: {name}
PATTERN DATA:
- Overall attendance: {pattern.get('overall_rate', 'n/a')}%
- Recent 4-week rate: {pattern.get('recent_4w_rate', 'n/a')}%
- 4-week change: {pattern.get('delta', 'n/a')} points
- Projected next week: {pattern.get('projected_next_week', 'n/a')}%
- Risk level: {pattern.get('risk', 'n/a')}
- Total absences (period): {pattern.get('total_absent', 0)}
- Total late check-ins (period): {pattern.get('total_late', 0)}
- Average check-in time: {pattern.get('avg_checkin', 'n/a')}
- Worst weekday: {pattern.get('worst_weekday', 'n/a')}
- Problematic patterns identified: {pattern.get('problems', [])}

Return STRICT JSON ONLY in this exact shape:
{{
  "summary":   "<one-line summary, max 120 chars>",
  "root_cause_hypothesis": "<your best guess at WHY this pattern exists, 1-2 sentences>",
  "tips": ["<actionable tip 1>", "<actionable tip 2>", "<actionable tip 3>"],
  "manager_action": "<one concrete thing the manager should do this week>",
  "tone": "<one of: supportive / cautionary / celebratory>"
}}
Be specific. If the employee is healthy (Low risk, no problems), celebrate and recommend stretch goals
instead of corrective tips. NEVER moralise or shame."""
        resp = client_genai.models.generate_content(
            model=settings.get("gemini_model", "gemini-2.5-flash"),
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.5,
            ),
        )
        raw = (resp.text or "").strip()
        return jsonify(json.loads(raw) if raw else {})
    except Exception as e:
        return jsonify({
            "summary": f"Unable to fetch AI tip: {str(e)[:100]}",
            "tips": [],
            "manager_action": "",
            "tone": "supportive",
            "error": str(e),
        }), 500


# =====================================================================
# MATRIX CATEGORIES + CHAT + HISTORY + SETTINGS + HEALTH
# =====================================================================


# =====================================================================
# /api/matrix-categories
# =====================================================================
@router.get("/api/matrix-categories")
def get_matrix_categories():
    settings = load_settings()
    return settings.get("matrix_categories", [])


@router.post("/api/matrix-categories")
async def save_matrix_categories(req: Request):
    settings = load_settings()
    data = await req.json()
    settings["matrix_categories"] = data.get("categories", [])
    state.save_settings(settings)
    return {"ok": True, "categories": settings["matrix_categories"]}
