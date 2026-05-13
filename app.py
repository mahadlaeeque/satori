"""
Satori Attendance Intelligence Chatbot
======================================
Flask web app with settings management, persistent chat history,
conversation memory, and voice I/O. Powered by Google Gemini + BigQuery.

Smart routing: general chat handled by Gemini API alone,
data-specific questions routed through BigQuery.
"""

import os
import json
import uuid
from datetime import datetime
from google import genai
from google.genai import types
from google.cloud import bigquery
import math
from flask import Flask, render_template, request, jsonify, Response
import requests as http_requests

# Secret Manager integration — loads API keys securely
from secret_manager import get_secret, clear_cache as clear_secret_cache, is_secret_key, mask_secret

# State store (Firestore in production, JSON fallback in dev — driven by
# SATORI_STATE_BACKEND env var; see firestore_client.py)
from firestore_client import store as state_store

# =====================================================================
# FILE PATHS FOR PERSISTENCE
# =====================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
HISTORY_FILE = os.path.join(BASE_DIR, "chat_history.json")

# =====================================================================
# DEFAULT SETTINGS
# =====================================================================
DEFAULT_SETTINGS = {
    "gemini_api_key": "",  # Loaded from Secret Manager — do NOT store plaintext here
    "gemini_model": "gemini-2.5-flash",
    "gcp_project": "ai-vertex-mahad",
    "bq_dataset": "Satori_Project",
    "bq_tables": [
        {
            "table_name": "Attendance_Data",
            "schema_description": """Daily attendance records for TMC employees. Each row = one employee's attendance for one day.

DATE: attendance_date (DATE)
EMPLOYEE: employee_id (INTEGER), employee_name (STRING), employee_email (STRING), personal_no (STRING)
CHECK-IN: checkin_time (STRING — e.g. "2026-04-24 09:37:00"), checkin_latitude (FLOAT), checkin_longitude (FLOAT), checkin_is_permitted_location (INTEGER), checkin_remarks (STRING)
CHECK-OUT: checkout_time (STRING — e.g. "2026-04-24 18:00:00"), checkout_latitude (FLOAT), checkout_longitude (FLOAT), checkout_is_permitted_location (INTEGER), checkout_remarks (STRING)
STATUS FLAGS (INTEGER 1/0): attendance_status, is_present, is_absent, is_on_leave, is_holiday, is_weekend, is_remote, is_missing_punch, has_missing_attendance
TEXT STATUS: attendance_status_text (STRING) — "Present", "Absent", "On Leave", "Holiday", "Weekend"
LEAVE/REMOTE: leave_type_name (STRING), remote_type_name (STRING), holiday_name (STRING)
TOTALS: total_checkins (INTEGER), total_checkouts (INTEGER)

IMPORTANT NOTES:
- checkin_time and checkout_time are STRING columns, NOT TIMESTAMP. ALWAYS cast before time functions: SAFE_CAST(checkin_time AS TIMESTAMP).
- "Late" = EXTRACT(TIME FROM SAFE_CAST(checkin_time AS TIMESTAMP)) > TIME(9,30,0)
- Working hours = TIMESTAMP_DIFF(SAFE_CAST(checkout_time AS TIMESTAMP), SAFE_CAST(checkin_time AS TIMESTAMP), MINUTE)/60.0
- attendance_date is DATE (not DATETIME). Filter with: attendance_date = DATE('2026-04-24')
- This table does NOT have employee_location. For location/city filtering, JOIN with Employee_Data on employee_name = Resource_Name and filter by Employee_Location."""
        },
        {
            "table_name": "Timesheet_Data",
            "schema_description": "AUTO_DETECT"
        },
        {
            "table_name": "Allocation_data",
            "schema_description": "AUTO_DETECT"
        },
        {
            "table_name": "Employee_Data",
            "schema_description": "AUTO_DETECT"
        }
    ],
    "matrix_categories": [
        {
            "id": "reliability",
            "name": "Attendance Reliability",
            "weight": 50,
            "formula": "presence_rate (days present ÷ total working days × 100)",
            "reasoning": "Measures how consistently an employee shows up. Presence rate = (days present ÷ total working days) × 100, excluding holidays and weekends. Source: Attendance_Data. 5 ★ = ≥95% attendance."
        },
        {
            "id": "engagement",
            "name": "Project Engagement",
            "weight": 50,
            "formula": "avg_allocation% × 0.6 + min(projects÷5, 1) × 40",
            "reasoning": "Measures how actively an employee is engaged across live projects. Components: (1) Average allocation % from Allocation_data — fully allocated = 60 points. (2) Distinct active project count capped at 5 projects = 40 points. Source: Allocation_data. 5 ★ = 100% allocated across 5+ projects."
        }
    ],
    "preferred_voice": "default",
    "gemini_tts_voice": "Leda",
    "tts_provider": "gemini",
    "tts_instructions": """Read the following text aloud as a professional, friendly voice assistant named Satori.

PRONUNCIATION RULES (CRITICAL):
- Times like 9:09 must be read as "nine oh nine AM", 14:30 as "two thirty PM", 6:00 as "six o'clock". NEVER say "colon" or read digits separately.
- Dates like 2025-04-01 should be read as "April first, twenty twenty-five". NEVER read dashes or hyphens in dates.
- South Asian names must be pronounced naturally: Mahad (mah-HAAD), Adeel (ah-DEEL), Fatima (FAA-ti-mah), Bilal (bi-LAAL), Hamza (HAM-zah).
- Percentages: 85% reads as "eighty-five percent".
- Numbers in data: "3/15 employees" reads as "three out of fifteen employees".
- Roman Urdu words should flow naturally in the sentence.

STYLE:
- Speak clearly, at a steady conversational pace.
- Sound warm and professional, like a helpful colleague briefing you.
- Emphasize key data points (names, times, counts) slightly.
- Keep a consistent tone throughout — do not change accent or personality mid-sentence.

TRANSCRIPT:
""",
    "agent_name": "Satori",
    "agent_instructions": """You are Satori, TMC's Capability Intelligence Agent. You help managers and HR teams understand employee attendance patterns, timesheets, and resource allocation.

PERSONALITY:
- Friendly, professional, and concise
- Use specific numbers, names, and dates in answers
- Format times in 12-hour format (e.g., 9:37 AM)
- Round percentages to 1 decimal place
- Never mention SQL, queries, tables, or columns to the user
- If data seems unusual, flag it as a potential data quality issue
- If no records found, suggest the user rephrase their question
- You can also handle general conversation — greetings, follow-ups, clarifications, small talk
- When the user refers back to something discussed earlier (e.g., "what about yesterday?" after asking about a specific employee), use conversation context to understand who/what they mean
- You have access to multiple data sources: attendance records, timesheet data, and allocation data. Use the right source(s) based on the question.

OUTPUT FORMATTING (CRITICAL):
When generating your final response, you must always return the output in HTML format so that it can be displayed properly to the user. Make sure to format the response using the following rules: wrap all paragraphs in <p> tags, highlight important information using <strong>, format lists with <ul> and <li> tags, and use <br> for line breaks where necessary. Do not include the <html>, <head>, or <body> tags in your output—only the content inside the body should be returned.

Here is an example of the correct HTML format for your response:

<p>According to the policy, employees are entitled to <strong>5 sick leaves</strong> per year.</p>
<p>Key points from the policy:</p>
<ul>
<li>Sick leaves are non-transferable.</li>
<li>A medical certificate is required for leaves exceeding <strong>3 days</strong>.</li>
</ul>""",
    "sql_rules": """- ONLY write SELECT statements. NEVER use INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, or TRUNCATE.
- ONLY use columns listed in the schema. Do not invent columns.
- For "today", use CURRENT_DATE()
- For "this week", use DATE_TRUNC(CURRENT_DATE(), WEEK(MONDAY))
- For "this month", use DATE_TRUNC(CURRENT_DATE(), MONTH)
- For "yesterday", use DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
- checkin_time and checkout_time are STRING columns (NOT TIMESTAMP). ALWAYS cast before time functions: SAFE_CAST(checkin_time AS TIMESTAMP). NEVER use EXTRACT or TIME directly on these columns without casting first.
- To check if someone was "late", use: EXTRACT(TIME FROM SAFE_CAST(checkin_time AS TIMESTAMP)) > TIME(9, 30, 0)
- To calculate working hours, use: TIMESTAMP_DIFF(SAFE_CAST(checkout_time AS TIMESTAMP), SAFE_CAST(checkin_time AS TIMESTAMP), MINUTE) / 60.0
- attendance_date is DATE. Filter with: attendance_date = DATE('2026-04-24')
- Attendance_Data does NOT have employee_location. For location/city filtering, JOIN with Employee_Data on employee_name = Resource_Name, then filter by Employee_Data.Employee_Location.
- Employee_Data does NOT have employee_id. Use Resource_Name or Employee_Code for joins.
- When filtering by employee name, use LOWER() and LIKE for partial matching
- Limit results to 20 rows unless the user asks for more or the query is an aggregation
- Order results meaningfully (by date DESC, by name ASC, by count DESC, etc.)
- If the question requires data from multiple tables, use JOINs on common columns.
- Choose the correct table based on the question: attendance for check-ins/status, timesheet for hours/projects, allocation for resource assignments.
- Return ONLY the SQL query. No explanation, no markdown, no backticks."""
}


# =====================================================================
# SETTINGS MANAGEMENT
# =====================================================================
def load_settings():
    """Load settings from the configured state backend, falling back to defaults.

    Backend is selected by SATORI_STATE_BACKEND (json | firestore | auto).
    All post-load fix-ups (default merge, secret manager lookup, matrix migration)
    happen here so callers don't have to care about the persistence layer.
    """
    try:
        saved = state_store.load_settings() or {}
    except Exception:
        saved = {}

    # Auto-migrate: old single-table → multi-table
    if "bq_table" in saved and "bq_tables" not in saved:
        saved["bq_tables"] = [{
            "table_name": saved.pop("bq_table"),
            "schema_description": saved.pop("schema_description", "AUTO_DETECT")
        }]
    merged = {**DEFAULT_SETTINGS, **saved}
    # Ensure bq_tables from saved overrides the default (not merge)
    if "bq_tables" in saved:
        merged["bq_tables"] = saved["bq_tables"]
    # Load API keys from Secret Manager (not from the state store)
    for key in ("gemini_api_key",):
        secret_val = get_secret(key)
        if secret_val:
            merged[key] = secret_val
    # Auto-migrate: remove deprecated categories (availability, experience)
    # and add project_engagement if missing
    if "matrix_categories" in merged:
        cat_ids = {c["id"] for c in merged["matrix_categories"]}
        deprecated = {"availability", "experience"}
        if deprecated & cat_ids and "project_engagement" not in cat_ids:
            merged["matrix_categories"] = [c for c in merged["matrix_categories"] if c["id"] not in deprecated]
            merged["matrix_categories"].append({
                "id": "project_engagement", "name": "Project Engagement", "weight": 35,
                "reasoning": "Scores employees higher based on the number of active projects they are currently allocated to. More projects indicates greater involvement and contribution across the organisation. Normalised to 0-100 (capped at 5 projects = 100%). Derived from Allocation_data: distinct project count per employee."
            })
            # Rebalance weights if needed
            total_w = sum(c["weight"] for c in merged["matrix_categories"])
            if total_w != 100:
                for c in merged["matrix_categories"]:
                    c["weight"] = round(c["weight"] / total_w * 100)
    return merged


def save_settings(settings):
    """Persist settings via the configured state backend."""
    # Don't persist secrets — they live in Secret Manager only
    clean = {k: v for k, v in settings.items() if not is_secret_key(k)}
    state_store.save_settings(clean)


# =====================================================================
# CHAT HISTORY MANAGEMENT
# =====================================================================
def load_history():
    """Return all conversations via the configured state backend."""
    return state_store.load_history()


def save_history(history):
    """Persist chat history via the configured state backend.

    Note: in Firestore mode this only syncs metadata (title, timestamps) and
    deletions — message subcollections are written by add_message_to_conversation.
    """
    state_store.save_history(history)


def get_conversation(conversation_id):
    """Get a specific conversation by ID (with full messages)."""
    return state_store.get_conversation(conversation_id)


def get_conversation_messages(conversation_id, limit=20):
    """Get the most recent `limit` messages from a conversation, oldest-first."""
    return state_store.get_conversation_messages(conversation_id, limit=limit)


def add_message_to_conversation(conversation_id, role, content):
    """Add a message to a conversation. Creates the conversation if missing."""
    return state_store.add_message_to_conversation(conversation_id, role, content)


def format_chat_history(messages):
    """Format recent messages into a readable string for Gemini context."""
    if not messages:
        return ""
    lines = []
    for msg in messages:
        role = "User" if msg["role"] == "user" else "Satori"
        lines.append(f"{role}: {msg['content']}")
    return "\n".join(lines)


# =====================================================================
# GEMINI + BIGQUERY ENGINE
# =====================================================================
_genai_client = None
_bq_client = None
_current_api_key = None


def get_genai_client(settings):
    """Get or create Gemini client (recreates if API key changed)."""
    global _genai_client, _current_api_key
    api_key = settings.get("gemini_api_key", "")
    if _genai_client is None or _current_api_key != api_key:
        _genai_client = genai.Client(api_key=api_key)
        _current_api_key = api_key
    return _genai_client


def get_bq_client(settings):
    """Get or create BigQuery client."""
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=settings["gcp_project"])
    return _bq_client


def build_full_table_name(settings, table_name):
    """Build fully qualified BigQuery table name."""
    return f"`{settings['gcp_project']}.{settings['bq_dataset']}.{table_name}`"


def build_full_table(settings):
    """Legacy helper — returns first table's fully qualified name."""
    tables = settings.get("bq_tables", [])
    if tables:
        return build_full_table_name(settings, tables[0]["table_name"])
    # Backward compat with old single-table settings
    return f"`{settings['gcp_project']}.{settings['bq_dataset']}.{settings.get('bq_table', '')}`"


def get_all_table_names(settings):
    """Return list of fully qualified table names."""
    tables = settings.get("bq_tables", [])
    if tables:
        return [build_full_table_name(settings, t["table_name"]) for t in tables]
    return [build_full_table(settings)]


def build_schema_context(settings):
    """Build a combined schema description for all configured tables."""
    tables = settings.get("bq_tables", [])
    if not tables:
        # Backward compat
        full_table = build_full_table(settings)
        return f"You have access to a BigQuery table: {full_table}\n\n{settings.get('schema_description', '')}"

    parts = [f"You have access to {len(tables)} BigQuery table(s) in dataset `{settings['gcp_project']}.{settings['bq_dataset']}`:\n"]
    for i, t in enumerate(tables, 1):
        full = build_full_table_name(settings, t["table_name"])
        schema = t.get("schema_description", "Schema not yet documented.")
        if schema == "AUTO_DETECT":
            schema = "(Schema will be auto-detected. Columns available at query time.)"
        parts.append(f"--- TABLE {i}: {full} ---\n{schema}\n")
    return "\n".join(parts)


# =====================================================================
# STEP 1: CLASSIFY — Does this need BigQuery or just general chat?
# =====================================================================
def classify_question(question, chat_history_str, settings):
    """Use Gemini to decide if the question needs a database lookup or is general chat."""
    prompt = f"""You are a routing classifier for TMC's Satori workforce intelligence platform.

Given the conversation history and the latest user message, decide if the user's message requires looking up data from the database, or if it can be answered through general conversation alone.

CONVERSATION HISTORY:
{chat_history_str if chat_history_str else "(no prior messages)"}

LATEST USER MESSAGE: "{question}"

The platform has access to 8 data sources:
1. Attendance — check-ins, check-outs, absences, leave, lateness, presence, remote work
2. Timesheets — ticket IDs, project codes, hours logged, task descriptions, approval status, ticket subjects
3. Resource Allocation — project assignments, allocation percentages, bench status, competency, weekly allocations
4. Employee Data — employee codes, positions, hierarchy, locations, employment type, status
5. Sales Accounts — customer accounts, tiers (A/B/C), visit tracking, dormant flags, account coverage
6. Sales AM Scorecard — account manager targets, pipeline, win rates, book size, revenue achievement
7. Sales Pipeline & Revenue — plan vs achievement, CRM pipeline health, coverage ratios, deal counts, hunting gaps
8. Sales KPIs & Workload — KPI definitions, weights, workload feasibility, field day capacity

RULES:
- Reply "DATA" if the message asks about ANY of these: attendance, check-ins, absences, leave, working hours, timesheets, tickets, ticket IDs, project hours, project assignments, resource allocation, employee profiles, who is on bench, project codes, logged hours, task status, employee positions, competencies, sales accounts, account coverage, visits, tiers, dormant accounts, pipeline, revenue targets, win rate, AM scorecard, KPIs, hunting gap, workload feasibility, field days, coverage ratio, or any question that needs real employee/workforce/sales data to answer.
- Reply "DATA" if the user is asking a follow-up about previously discussed data (e.g., "what about yesterday?", "and for March?", "how about Ahmed?", "show me the ticket details", "which AMs?", "what about Karachi accounts?") — even if it seems vague, the context makes it a data question.
- Reply "CHAT" if the message is a greeting, thank you, general question, small talk, opinion, clarification about how the bot works, or anything that does NOT need workforce data.

Reply with ONLY one word: DATA or CHAT"""

    client = get_genai_client(settings)
    model = settings.get("gemini_model", "gemini-2.5-flash")
    response = client.models.generate_content(model=model, contents=prompt)
    result = response.text.strip().upper()
    return "DATA" if "DATA" in result else "CHAT"


# =====================================================================
# STEP 2a: GENERAL CHAT — Answer without BigQuery
# =====================================================================
def get_language_instruction(lang):
    """Return a prompt instruction based on language preference."""
    if lang == "ur":
        return """LANGUAGE RULE (MANDATORY): You MUST respond ENTIRELY in Roman Urdu (Urdu written in English/Latin letters).
Do NOT use English. Do NOT mix English and Urdu. Write everything in Roman Urdu.
Example: Instead of "He was present" write "Woh haazir tha".
Instead of "checked in at 9:37 AM" write "9:37 AM par check-in kiya".
Numbers, times, and dates can stay in English numerals (e.g., 9:37 AM, 1st April) but all words must be Roman Urdu."""
    return ""


def general_chat(question, chat_history_str, settings, lang="en"):
    """Handle general conversation using just Gemini, with full conversation memory."""
    lang_instruction = get_language_instruction(lang)
    prompt = f"""{settings['agent_instructions']}

{lang_instruction}

You are {settings.get('agent_name', 'Satori')}, TMC's AI workforce intelligence assistant.

CONVERSATION HISTORY:
{chat_history_str if chat_history_str else "(this is the start of the conversation)"}

USER: {question}

Respond naturally and helpfully. You have access to 8 data sources: Attendance records, Timesheet data (tickets, project hours, tasks), Resource Allocation (project assignments, bench status, competencies), Employee Data (profiles, positions, hierarchy), and Sales Account Coverage data (customer accounts, tiers, visit tracking, AM scorecards, revenue targets, pipeline health, KPIs, hunting gaps, workload feasibility, dormant accounts). Let the user know you can look up any of this data if relevant. Keep responses concise and friendly."""

    client = get_genai_client(settings)
    model = settings.get("gemini_model", "gemini-2.5-flash")
    response = client.models.generate_content(model=model, contents=prompt)
    return response.text.strip()


# =====================================================================
# STEP 2b: DATA QUERY — Resolve context, generate SQL, run BigQuery
# =====================================================================
def resolve_question(question, chat_history_str, settings):
    """Use conversation context to resolve ambiguous references into a standalone question."""
    if not chat_history_str:
        return question

    prompt = f"""Given this conversation history and the latest user message, rewrite the user's message as a fully self-contained question that includes all necessary context (employee names, dates, etc.) from the conversation.

CONVERSATION HISTORY:
{chat_history_str}

LATEST USER MESSAGE: "{question}"

RULES:
- If the message already contains all necessary context, return it unchanged.
- If the message refers to a previously mentioned person (e.g., "what about him?", "and for yesterday?"), fill in the missing details from the conversation.
- Return ONLY the rewritten question. No explanation.

REWRITTEN QUESTION:"""

    client = get_genai_client(settings)
    model = settings.get("gemini_model", "gemini-2.5-flash")
    response = client.models.generate_content(model=model, contents=prompt)
    resolved = response.text.strip().strip('"')
    return resolved if resolved else question


def generate_sql(question, settings):
    """Use Gemini to convert natural language to BigQuery SQL."""
    schema = build_schema_context(settings)
    table_names_str = ", ".join(get_all_table_names(settings))
    prompt = f"""You are a BigQuery SQL expert for TMC's Satori Capability Intelligence system.

Given these tables and schemas:
{schema}

The user asks: "{question}"

Generate a single BigQuery SQL query to answer this question.

TABLE SELECTION GUIDE (CRITICAL — choose the right table):

WORKFORCE TABLES:
- Attendance_Data: For ALL attendance queries — check-ins, check-outs, presence, absences, leave, lateness, recent and historical. Does NOT have location — for location queries JOIN with Employee_Data. IMPORTANT: Always use Attendance_Data (not "Attendance").
- Timesheet_Data: For questions about tickets, ticket IDs, project hours, logged hours, task descriptions, ticket status, project codes, work logs, approvals, ticket subjects, TICKET_HOURS
- Allocation_data: For questions about project assignments, allocation percentages, bench status, employee competency, which projects someone is assigned to, forecast flags, weekly allocations
- Employee_Data: For questions about employee profiles, positions, hierarchy, employment type, employee status. Has Employee_Location (city), Employee_Code, Resource_Name. Use this table for location/city filtering via JOINs. Does NOT have employee_id — use Resource_Name or Employee_Code for joins.

SALES / ACCOUNT COVERAGE TABLES:
- Sales_Accounts: For customer accounts, account lists, tiers (A/B/C), visit counts per month/quarter, zero-visit accounts, dormant flags, coverage by AM or VP. Main account-level table (~359 rows). Has VP, AM, Location, Account, Tier, Dormant, Jan_Visits, Feb_Visits, Mar_Visits, Q1_Visits, Zero_Visit.
- Sales_AM_Scorecard: For AM performance summaries — book size, tier breakdown, revenue targets, Q1 achievement, open pipeline, historical win rate. Has col_2026_Target (USD), Q1_ACH (USD), Open_Pipeline (USD), Hist_Win_Rate (decimal 0-1). 8 AMs total.
- Sales_Plan_vs_Pipeline: For revenue plan attainment — target vs actual, coverage ratio, pipeline health status. Has col_2026_Target, Q1_Target, Q1_ACH, Q1_of_Plan, Remaining_2026, CRM_Pipeline, Coverage_Ratio, Status, Action.
- Sales_Pipeline_Health: For pipeline analysis across ALL salespeople (not just AMs) — open pipeline, deal counts, historical won/lost, win rate. Has Salesperson, Formal_Territory, Open_Pipeline, Open_Deals, Historical_Won, Historical_Lost, Win_Rate_by.
- Sales_Hunting_Gap: For new business development — hunting quotas, recommended meetings, target industries, lead sources per AM.
- Sales_KPI_Scorecard: For KPI definitions — the 10 KPIs, their weights, formulas, targets, cadence. REFERENCE table only.
- Sales_Dormant_Accounts: For dormant account analysis — accounts recommended for removal. ~21 accounts.
- Sales_Workload_Feasibility: For AM capacity analysis — required vs available field days, utilisation, slack/overload.

WORKFORCE JOIN KEYS:
- Attendance_Data.employee_name can join with Employee_Data.Resource_Name
- Timesheet_Data.TICKET_USER_ID can match Attendance_Data.employee_id
- Allocation_data.employee_id (format "E-1234") matches Employee_Data.Employee_Code

SALES TABLE JOIN KEYS:
- Sales_Accounts.AM = Sales_AM_Scorecard.AM = Sales_Plan_vs_Pipeline.AM = Sales_Hunting_Gap.AM = Sales_Workload_Feasibility.AM (join on AM name)
- Sales_Accounts.VP = Sales_AM_Scorecard.VP = Sales_Dormant_Accounts.VP (join on VP name)
- Sales_Pipeline_Health.Salesperson may match Sales_AM_Scorecard.AM for formal AMs
- Sales tables do NOT join with Attendance/Timesheet/Employee tables (separate domains)

SALES-SPECIFIC RULES:
- Dollar amounts (col_2026_Target, Q1_ACH, Open_Pipeline, CRM_Pipeline, etc.) are raw FLOAT64 numbers. Format as currency in display.
- Win rates (Hist_Win_Rate, Win_Rate_by) are decimals 0-1. Multiply by 100 for percentage.
- Q1_of_Plan and Utilisation are also ratios (0-1).
- Coverage_Ratio >= 3.0 is healthy, 2-3 is tight, < 2 is critical.
- Tier cadence: A = 2x/month, B = 1x/month, C = 1x/quarter.
- When filtering by AM or VP name, use LOWER() LIKE for flexible matching.
- For "top AMs" or "best performers", use Sales_AM_Scorecard or Sales_Plan_vs_Pipeline ordered by relevant metrics.

ATTENDANCE-SPECIFIC RULES (Attendance_Data table):
- ALWAYS use Attendance_Data for ALL attendance queries (never use "Attendance" without "_Data").
- checkin_time and checkout_time are STRING columns (NOT TIMESTAMP). ALWAYS cast before time functions: SAFE_CAST(checkin_time AS TIMESTAMP).
- To get the time portion: EXTRACT(TIME FROM SAFE_CAST(checkin_time AS TIMESTAMP))
- To check if someone was "late": EXTRACT(TIME FROM SAFE_CAST(checkin_time AS TIMESTAMP)) > TIME(9, 30, 0)
- To calculate working hours: TIMESTAMP_DIFF(SAFE_CAST(checkout_time AS TIMESTAMP), SAFE_CAST(checkin_time AS TIMESTAMP), MINUTE) / 60.0
- attendance_date is DATE (not DATETIME). Filter with: attendance_date = DATE('2026-04-24')
- Attendance_Data does NOT have a location/city column. For location filtering (e.g. "in Lahore"), JOIN with Employee_Data: employee_name = Employee_Data.Resource_Name, then filter by Employee_Data.Employee_Location.
- Employee_Data does NOT have employee_id. It has Employee_Code and Resource_Name. Never reference employee_id on Employee_Data.

TIMESHEET-SPECIFIC RULES:
- TICKET_HOURS is a STRING column — use SAFE_CAST(TICKET_HOURS AS FLOAT64) for calculations
- Use TICKET_PROJECT_LABEL for project names, TICKET_PROJECT_CODE for project codes
- Use TICKET_NUMBER or TICKET_ID for ticket lookups
- Use TICKET_STATUS for approval status (Approved, Submitted, Saved, Rejected)
- DATE_KEY is a DATETIME column — use DATE(DATE_KEY) for date filtering, e.g. DATE(DATE_KEY) >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY). Do NOT compare DATE_KEY to string literals or use CAST(DATE_KEY AS STRING) in WHERE clauses.

ALLOCATION-SPECIFIC RULES:
- allocation_percent is INT64, employee_id is STRING (format "E-1234")
- Use Flag for status (Allocated, Bench, etc.)
- Use emp_competency for employee skills/competency
- Use Date for date filtering, project_id for project lookups

GENERAL RULES:
- ONLY write SELECT statements. NEVER use INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, or TRUNCATE.
- When filtering by employee name, ALWAYS use LOWER() LIKE LOWER('%partial_name%') for flexible matching.
- If the user says a date like "1st April" without a year, assume the current year (2026).
- Limit results to 20 rows unless the user asks for more or the query is an aggregation.

ADDITIONAL RULES:
{settings['sql_rules']}
Available tables (use fully qualified names): {table_names_str}"""

    client = get_genai_client(settings)
    model = settings.get("gemini_model", "gemini-2.5-flash")
    response = client.models.generate_content(model=model, contents=prompt)
    sql = response.text.strip().strip("`").strip()
    if sql.lower().startswith("sql"):
        sql = sql[3:].strip()
    return sql


def validate_sql(sql):
    """Ensure the SQL is a safe SELECT statement."""
    sql_upper = sql.upper().strip()
    if not sql_upper.startswith("SELECT"):
        return False
    dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "MERGE"]
    for word in dangerous:
        if word in sql_upper.split():
            return False
    return True


def run_query(sql, settings):
    """Execute SQL against BigQuery."""
    job = get_bq_client(settings).query(sql)
    return job.to_dataframe()


def format_answer(question, data, chat_history_str, settings, lang="en"):
    """Use Gemini to convert raw query results into natural language, with conversation context."""
    data_str = data.to_string(index=False) if len(data) <= 50 else data.head(50).to_string(index=False) + f"\n... ({len(data)} total rows)"
    lang_instruction = get_language_instruction(lang)

    prompt = f"""{settings['agent_instructions']}

{lang_instruction}

CONVERSATION HISTORY:
{chat_history_str if chat_history_str else "(first message)"}

The user asked: "{question}"

The database returned this data:
{data_str}

Write a clear, concise, and friendly answer in natural language. Use the conversation history to provide context-aware responses."""

    client = get_genai_client(settings)
    model = settings.get("gemini_model", "gemini-2.5-flash")
    response = client.models.generate_content(model=model, contents=prompt)
    return response.text.strip()


# =====================================================================
# MAIN ORCHESTRATOR
# =====================================================================
def _extract_sources_from_sql(sql, settings):
    """Extract human-readable table names from a SQL query."""
    import re
    sources = []
    table_display = {
        "Attendance_Data": "Attendance Records",
        "Timesheet_Data": "Timesheet Data",
        "Allocation_data": "Resource Allocation",
        "Employee_Data": "Employee Data"
    }
    tables = settings.get("bq_tables", [])
    for t in tables:
        tname = t["table_name"]
        if tname.lower() in sql.lower():
            sources.append(table_display.get(tname, tname))
    return sources


def ask_satori(question, conversation_id, settings, lang="en"):
    """Main function: classifies, routes, and answers with full conversation memory."""
    try:
        # Get conversation history for context
        recent_messages = get_conversation_messages(conversation_id, limit=10)
        chat_history_str = format_chat_history(recent_messages)

        # Step 1: Classify — does this need data or is it general chat?
        route = classify_question(question, chat_history_str, settings)

        if route == "CHAT":
            # General conversation — no BigQuery needed
            answer = general_chat(question, chat_history_str, settings, lang=lang)
            return {"answer": answer, "sql": "", "error": False, "route": "chat", "sources": []}

        # Step 2: Data query — resolve context first
        resolved_question = resolve_question(question, chat_history_str, settings)

        # Step 3: Generate SQL from the resolved question
        sql = generate_sql(resolved_question, settings)

        if not validate_sql(sql):
            return {
                "answer": "I can only answer questions that involve reading data. I can't modify any data.",
                "sql": sql, "error": False, "route": "data", "sources": []
            }

        # Step 4: Run query
        print(f"[SQL] Generated: {sql}")
        try:
            results = run_query(sql, settings)
        except Exception as e:
            print(f"[SQL ERROR] {str(e)}")
            # Auto-retry with error context
            schema = build_schema_context(settings)
            retry_prompt = f"""The previous SQL query failed with this error: {str(e)}

Original question: "{resolved_question}"
Failed SQL: {sql}
Schema: {schema}

CRITICAL RULES:
- Use the "Attendance" table (not Attendance_Data) for recent/current date queries like 24th April 2026.
- checkin_time and checkout_time are STRING columns (not TIMESTAMP). ALWAYS cast them first: SAFE_CAST(checkin_time AS TIMESTAMP) before using EXTRACT or TIME functions.
- attendance_date is DATE (not DATETIME). Filter with: attendance_date = DATE('2026-04-24')
- Neither Attendance nor Attendance_Data has employee_location. For location/city filtering, JOIN with Employee_Data: employee_name = Employee_Data.Resource_Name, then filter by Employee_Data.Employee_Location.
- Employee_Data does NOT have employee_id. It has Employee_Code and Resource_Name. NEVER reference employee_id on Employee_Data.

Generate a corrected BigQuery SQL query. Return ONLY the SQL, no explanation."""
            client = get_genai_client(settings)
            model = settings.get("gemini_model", "gemini-2.5-flash")
            response = client.models.generate_content(model=model, contents=retry_prompt)
            sql = response.text.strip().strip("`").strip()
            if sql.lower().startswith("sql"):
                sql = sql[3:].strip()
            if not validate_sql(sql):
                return {"answer": "I had trouble understanding that question. Could you rephrase it?", "sql": sql, "error": True, "route": "data", "sources": []}
            results = run_query(sql, settings)

        # Extract data sources from the SQL
        sources = _extract_sources_from_sql(sql, settings)

        # Step 5: Format answer
        if results.empty:
            return {
                "answer": "I couldn't find any matching records. This might mean there's no data for the time period or criteria you specified. Try adjusting your question.",
                "sql": sql, "error": False, "route": "data", "sources": sources
            }

        answer = format_answer(resolved_question, results, chat_history_str, settings, lang=lang)
        return {"answer": answer, "sql": sql, "error": False, "route": "data", "sources": sources}

    except Exception as e:
        return {
            "answer": f"Sorry, I ran into an issue processing that question. Please try rephrasing it.\n\nTechnical detail: {str(e)}",
            "sql": "", "error": True, "route": "error", "sources": []
        }


# =====================================================================
# FLASK APP
# =====================================================================
app = Flask(__name__)

# Live voice agent (Gemini Multimodal Live API)
from voice_agent import register_voice_routes
register_voice_routes(app)


@app.route("/")
def index():
    settings = load_settings()
    return render_template("index.html", agent_name=settings.get("agent_name", "Satori"))


@app.route("/dashboard")
def dashboard():
    settings = load_settings()
    return render_template("dashboard.html", agent_name=settings.get("agent_name", "Satori"))


# ---- Attendance Data API ----

@app.route("/api/tables", methods=["GET"])
def get_tables():
    """Return list of configured table names."""
    settings = load_settings()
    tables = settings.get("bq_tables", [])
    return jsonify({"tables": [t["table_name"] for t in tables]})


@app.route("/api/sync-drive", methods=["POST"])
def sync_drive_data():
    """Trigger Google Drive → BigQuery sync for Account Coverage Plan."""
    try:
        from drive_sync import run_sync
        dry_run = request.json.get("dry_run", False) if request.is_json else False
        tables = run_sync(dry_run=dry_run)
        summary = {name: len(df) for name, df in tables.items()}
        return jsonify({"status": "success", "tables_loaded": summary, "dry_run": dry_run})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/table-schema/<table_name>", methods=["GET"])
def detect_table_schema(table_name):
    """Auto-detect schema from BigQuery INFORMATION_SCHEMA for a given table."""
    settings = load_settings()
    project = settings["gcp_project"]
    dataset = settings["bq_dataset"]
    try:
        client = get_bq_client(settings)
        sql = f"""SELECT column_name, data_type, is_nullable
        FROM `{project}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = '{table_name}'
        ORDER BY ordinal_position"""
        results = list(client.query(sql).result())
        if not results:
            return jsonify({"error": f"Table '{table_name}' not found or has no columns.", "columns": []}), 404
        columns = []
        schema_lines = []
        for r in results:
            columns.append({"name": r.column_name, "type": r.data_type, "nullable": r.is_nullable})
            schema_lines.append(f"- {r.column_name} ({r.data_type})")
        schema_text = f"Table: {table_name}\nColumns:\n" + "\n".join(schema_lines)
        return jsonify({"columns": columns, "schema_text": schema_text, "table_name": table_name})
    except Exception as e:
        return jsonify({"error": str(e), "columns": []}), 500


# ---- Gemini TTS (Primary) ----

GEMINI_TTS_VOICES = [
    {"voice_name": "Zephyr",        "style": "Bright",        "recommended": True},
    {"voice_name": "Puck",          "style": "Upbeat",        "recommended": True},
    {"voice_name": "Charon",        "style": "Informative",   "recommended": True},
    {"voice_name": "Kore",          "style": "Firm",          "recommended": True},
    {"voice_name": "Fenrir",        "style": "Excitable",     "recommended": False},
    {"voice_name": "Leda",          "style": "Youthful",      "recommended": True},
    {"voice_name": "Orus",          "style": "Firm",          "recommended": False},
    {"voice_name": "Aoede",         "style": "Breezy",        "recommended": False},
    {"voice_name": "Callirrhoe",    "style": "Easy-going",    "recommended": False},
    {"voice_name": "Autonoe",       "style": "Bright",        "recommended": False},
    {"voice_name": "Enceladus",     "style": "Breathy",       "recommended": False},
    {"voice_name": "Iapetus",       "style": "Clear",         "recommended": False},
    {"voice_name": "Umbriel",       "style": "Easy-going",    "recommended": False},
    {"voice_name": "Algieba",       "style": "Smooth",        "recommended": False},
    {"voice_name": "Despina",       "style": "Smooth",        "recommended": False},
    {"voice_name": "Erinome",       "style": "Clear",         "recommended": False},
    {"voice_name": "Algenib",       "style": "Gravelly",      "recommended": False},
    {"voice_name": "Rasalgethi",    "style": "Informative",   "recommended": False},
    {"voice_name": "Laomedeia",     "style": "Upbeat",        "recommended": False},
    {"voice_name": "Achernar",      "style": "Soft",          "recommended": False},
    {"voice_name": "Alnilam",       "style": "Firm",          "recommended": False},
    {"voice_name": "Schedar",       "style": "Even",          "recommended": False},
    {"voice_name": "Gacrux",        "style": "Mature",        "recommended": False},
    {"voice_name": "Pulcherrima",   "style": "Forward",       "recommended": False},
    {"voice_name": "Achird",        "style": "Friendly",      "recommended": False},
    {"voice_name": "Zubenelgenubi", "style": "Casual",        "recommended": False},
    {"voice_name": "Vindemiatrix",  "style": "Gentle",        "recommended": False},
    {"voice_name": "Sadachbia",     "style": "Lively",        "recommended": False},
    {"voice_name": "Sadaltager",    "style": "Knowledgeable", "recommended": False},
    {"voice_name": "Sulafat",       "style": "Warm",          "recommended": False},
]

GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"


def _convert_to_wav(audio_data: bytes, mime_type: str) -> bytes:
    """Convert raw PCM audio from Gemini TTS to WAV format."""
    import struct as _struct

    # Parse mime type for parameters (e.g. "audio/L16;rate=24000")
    bits_per_sample = 16
    rate = 24000
    for param in mime_type.split(";"):
        param = param.strip()
        if param.lower().startswith("rate="):
            try:
                rate = int(param.split("=", 1)[1])
            except (ValueError, IndexError):
                pass
        elif param.startswith("audio/L"):
            try:
                bits_per_sample = int(param.split("L", 1)[1])
            except (ValueError, IndexError):
                pass

    num_channels = 1
    bytes_per_sample = bits_per_sample // 8
    block_align = num_channels * bytes_per_sample
    byte_rate = rate * block_align
    data_size = len(audio_data)
    chunk_size = 36 + data_size

    header = _struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", chunk_size, b"WAVE", b"fmt ", 16, 1,
        num_channels, rate, byte_rate, block_align, bits_per_sample,
        b"data", data_size
    )
    return header + audio_data


@app.route("/api/gemini-tts/voices", methods=["GET"])
def get_gemini_tts_voices():
    """Return the Gemini TTS voice list + current selection."""
    settings = load_settings()
    return jsonify({
        "voices": GEMINI_TTS_VOICES,
        "selected_voice": settings.get("gemini_tts_voice", "Leda"),
        "model": GEMINI_TTS_MODEL,
    })


@app.route("/api/gemini-tts/speak", methods=["POST"])
def gemini_tts_speak():
    """Generate speech using Gemini TTS. Returns WAV audio."""
    settings = load_settings()
    api_key = settings.get("gemini_api_key", "")
    if not api_key:
        return jsonify({"error": "Gemini API key not configured"}), 400

    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text provided"}), 400

    voice_name = data.get("voice_name") or settings.get("gemini_tts_voice", "Leda")
    tts_instructions = settings.get("tts_instructions", "")
    full_text = f"{tts_instructions}{text}" if tts_instructions else text

    try:
        client = genai.Client(api_key=api_key)

        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=full_text)],
            ),
        ]
        config = types.GenerateContentConfig(
            temperature=1,
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            ),
        )

        # Streaming: collect all audio chunks
        audio_data = b""
        mime_type = "audio/L16;rate=24000"
        for chunk in client.models.generate_content_stream(
            model=GEMINI_TTS_MODEL,
            contents=contents,
            config=config,
        ):
            if chunk.candidates and chunk.candidates[0].content and chunk.candidates[0].content.parts:
                part = chunk.candidates[0].content.parts[0]
                if part.inline_data and part.inline_data.data:
                    audio_data += part.inline_data.data
                    if part.inline_data.mime_type:
                        mime_type = part.inline_data.mime_type

        if not audio_data:
            return jsonify({"error": "No audio generated"}), 500

        wav_data = _convert_to_wav(audio_data, mime_type)
        return Response(wav_data, mimetype="audio/wav",
                        headers={"Cache-Control": "no-cache"})

    except Exception as e:
        print(f"[Gemini TTS] Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/gemini-tts/preview", methods=["POST"])
def gemini_tts_preview():
    """Preview a Gemini TTS voice with a bilingual sample sentence."""
    settings = load_settings()
    api_key = settings.get("gemini_api_key", "")
    if not api_key:
        return jsonify({"error": "Gemini API key not configured"}), 400

    data = request.get_json(silent=True) or {}
    voice_name = data.get("voice_name", "Leda")
    tts_instructions = settings.get("tts_instructions", "")

    preview_text = "Hello, I am Satori, your Capability Intelligence Agent. Mahad checked in today at 9:09 AM and left at 6:30 PM. Adeel ki attendance bhi complete hai."
    full_text = f"{tts_instructions}{preview_text}" if tts_instructions else preview_text

    try:
        client = genai.Client(api_key=api_key)

        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=full_text)],
            ),
        ]
        config = types.GenerateContentConfig(
            temperature=1,
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            ),
        )

        audio_data = b""
        mime_type = "audio/L16;rate=24000"
        for chunk in client.models.generate_content_stream(
            model=GEMINI_TTS_MODEL,
            contents=contents,
            config=config,
        ):
            if chunk.candidates and chunk.candidates[0].content and chunk.candidates[0].content.parts:
                part = chunk.candidates[0].content.parts[0]
                if part.inline_data and part.inline_data.data:
                    audio_data += part.inline_data.data
                    if part.inline_data.mime_type:
                        mime_type = part.inline_data.mime_type

        if not audio_data:
            return jsonify({"error": "No audio generated"}), 500

        wav_data = _convert_to_wav(audio_data, mime_type)
        return Response(wav_data, mimetype="audio/wav",
                        headers={"Cache-Control": "no-cache"})

    except Exception as e:
        print(f"[Gemini TTS Preview] Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/attendance", methods=["GET"])
def get_attendance_data():
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
# DASHBOARD CHART DATA
# =====================================================================

@app.route("/api/chart-data", methods=["GET"])
def get_chart_data():
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

@app.route("/api/help", methods=["POST"])
def satori_help():
    """Answer questions about how to use the Satori platform using Gemini."""
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

@app.route("/api/availability", methods=["GET"])
def availability_engine():
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

@app.route("/api/skill-search", methods=["GET"])
def skill_search():
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

@app.route("/api/predictive-attendance", methods=["GET"])
def predictive_attendance():
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

def _discover_columns(bq_client, project, dataset, table_name):
    """Return set of column names for a table via INFORMATION_SCHEMA."""
    sql = f"""SELECT column_name FROM `{project}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
              WHERE table_name = '{table_name}'"""
    return {row.column_name for row in bq_client.query(sql).result()}


def _find_column(columns, candidates):
    """Find first matching column name from a list of candidates (case-insensitive)."""
    col_lower = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in col_lower:
            return col_lower[cand.lower()]
    return None


import re as _re

def _normalize_eid(raw):
    """Normalize employee ID: 'E-1571' -> '1571', 'I-2024' -> '2024', 'C-1604' -> '1604', 1571 -> '1571'."""
    s = str(raw).strip()
    # Strip any letter prefix(es) followed by optional dash: E-1571, I-2024, C-1604, EMP-100, etc.
    s = _re.sub(r'^[A-Za-z]+-?', '', s)
    # Strip leading zeros
    s = s.lstrip('0') or '0'
    return s


@app.route("/api/capability-matrix", methods=["GET"])
def capability_matrix():
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

@app.route("/api/departments", methods=["GET"])
def get_departments():
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


@app.route("/api/ai-staff-suggestions", methods=["POST"])
def ai_staff_suggestions():
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

@app.route("/api/employee-pattern", methods=["GET"])
def employee_pattern():
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


@app.route("/api/ai-productivity-tip", methods=["POST"])
def ai_productivity_tip():
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

@app.route("/api/matrix-categories", methods=["GET"])
def get_matrix_categories():
    settings = load_settings()
    return jsonify(settings.get("matrix_categories", []))


@app.route("/api/matrix-categories", methods=["POST"])
def save_matrix_categories():
    settings = load_settings()
    data = request.get_json()
    settings["matrix_categories"] = data.get("categories", [])
    save_settings(settings)
    return jsonify({"ok": True, "categories": settings["matrix_categories"]})


@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    question = data.get("question", "").strip()
    conversation_id = data.get("conversation_id", str(uuid.uuid4()))
    if not question:
        return jsonify({"error": "No question provided"}), 400
    settings = load_settings()
    lang = data.get("lang", "en")
    result = ask_satori(question, conversation_id, settings, lang=lang)
    add_message_to_conversation(conversation_id, "user", question)
    add_message_to_conversation(conversation_id, "bot", result["answer"])
    result["conversation_id"] = conversation_id
    return jsonify(result)


@app.route("/history", methods=["GET"])
def get_all_history():
    """Return all conversations (without full messages for the list view)."""
    history = load_history()
    summary = []
    for conv in history:
        # In Firestore mode, conv["messages"] is an empty placeholder; the real
        # count comes from the doc-level `_seq` field maintained by the store.
        msg_count = conv.get("_seq", len(conv.get("messages", [])))
        summary.append({
            "id": conv["id"],
            "title": conv["title"],
            "created_at": conv["created_at"],
            "updated_at": conv["updated_at"],
            "message_count": msg_count,
        })
    return jsonify(summary)


@app.route("/history/<conversation_id>", methods=["GET"])
def get_conversation_detail(conversation_id):
    """Return full messages for a specific conversation."""
    conv = get_conversation(conversation_id)
    if conv is None:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify(conv)


@app.route("/history/<conversation_id>", methods=["DELETE"])
def delete_conversation(conversation_id):
    """Delete a specific conversation."""
    state_store.delete_conversation(conversation_id)
    return jsonify({"status": "deleted"})


@app.route("/history/<conversation_id>/rename", methods=["POST"])
def rename_conversation(conversation_id):
    """Rename a conversation."""
    data = request.get_json()
    new_title = (data.get("title") or "").strip()
    if not new_title:
        return jsonify({"error": "Title required"}), 400
    state_store.rename_conversation(conversation_id, new_title)
    return jsonify({"status": "renamed"})


@app.route("/settings", methods=["GET"])
def get_settings():
    """Return current settings. Secret values are masked for security."""
    settings = load_settings()
    safe_settings = dict(settings)
    for key in ("gemini_api_key",):
        if safe_settings.get(key):
            safe_settings[key] = mask_secret(safe_settings[key])
    return jsonify(safe_settings)


@app.route("/settings", methods=["POST"])
def update_settings():
    """Update settings. Secret keys are NOT saved to disk — only to Secret Manager."""
    data = request.get_json()
    settings = load_settings()
    secret_updates = {}
    regular_updates = {}
    for key, value in data.items():
        if is_secret_key(key) and value:
            secret_updates[key] = value
        else:
            regular_updates[key] = value
    settings.update(regular_updates)
    save_settings(settings)
    if secret_updates:
        clear_secret_cache()
        settings.update(secret_updates)
    global _genai_client, _current_api_key, _bq_client
    if "gemini_api_key" in data:
        _genai_client = None
        _current_api_key = None
    if "gcp_project" in data:
        _bq_client = None
    safe_settings = dict(settings)
    for key in ("gemini_api_key",):
        if safe_settings.get(key):
            safe_settings[key] = mask_secret(safe_settings[key])
    return jsonify({"status": "saved", "settings": safe_settings})


@app.route("/settings/reset", methods=["POST"])
def reset_settings():
    """Reset settings to defaults. Secrets remain in Secret Manager."""
    save_settings(DEFAULT_SETTINGS)
    clear_secret_cache()
    global _genai_client, _bq_client, _current_api_key
    _genai_client = None
    _bq_client = None
    _current_api_key = None
    settings = load_settings()
    safe_settings = dict(settings)
    for key in ("gemini_api_key",):
        if safe_settings.get(key):
            safe_settings[key] = mask_secret(safe_settings[key])
    return jsonify({"status": "reset", "settings": safe_settings})


@app.route("/api/test-connection", methods=["POST"])
def test_connection():
    """Test BigQuery connection for all configured tables. Returns row count per table."""
    data = request.get_json() or {}
    settings = load_settings()
    project = data.get("gcp_project", settings["gcp_project"])
    dataset = data.get("bq_dataset", settings["bq_dataset"])
    tables_to_test = data.get("bq_tables", [])
    if not tables_to_test:
        tables_to_test = [t["table_name"] for t in settings.get("bq_tables", [])]
    if not tables_to_test:
        tables_to_test = [settings.get("bq_table", "")]
    try:
        global _bq_client
        if project != settings.get("gcp_project"):
            _bq_client = None
        client = bigquery.Client(project=project)
        results_list = []
        for tbl in tables_to_test:
            full_table = f"`{project}.{dataset}.{tbl}`"
            try:
                sql = f"SELECT COUNT(*) as total_rows FROM {full_table} LIMIT 1"
                job = client.query(sql)
                results = list(job.result())
                row_count = results[0].total_rows if results else 0
                results_list.append({"table": tbl, "status": "ok", "rows": row_count})
            except Exception as te:
                results_list.append({"table": tbl, "status": "error", "error": str(te)})
        ok_count = sum(1 for r in results_list if r["status"] == "ok")
        total_rows = sum(r.get("rows", 0) for r in results_list if r["status"] == "ok")
        if ok_count == len(results_list):
            msg = f"All {ok_count} table(s) connected! Total: {total_rows:,} rows"
            return jsonify({"status": "connected", "message": msg, "tables": results_list})
        else:
            msg = f"{ok_count}/{len(results_list)} table(s) connected. Check details below."
            return jsonify({"status": "partial", "message": msg, "tables": results_list})
    except Exception as e:
        error_str = str(e)
        hint = error_str
        if "Could not automatically determine credentials" in error_str:
            hint = "Google Cloud credentials not found. Run 'gcloud auth application-default login'"
        elif "Not found" in error_str:
            hint = f"Dataset not found. Please verify that '{dataset}' exists in BigQuery."
        elif "Permission" in error_str or "403" in error_str:
            hint = "Permission denied. Make sure your Google account has BigQuery access for this project."
        return jsonify({"status": "error", "message": hint, "error": error_str})


@app.route("/api/transliterate", methods=["POST"])
def transliterate():
    """Use Gemini to convert Urdu script text to Roman Urdu."""
    data = request.get_json()
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"result": ""})
    settings = load_settings()
    prompt = ("Convert the following Urdu text to Roman Urdu (Urdu written in English/Latin letters).\n"
              "Keep the meaning exactly the same. Do NOT translate to English — just transliterate to Roman Urdu.\n"
              "If the text is already in English or Roman Urdu, return it unchanged.\n\n"
              f"Text: {text}\n\nRoman Urdu:")
    try:
        client = get_genai_client(settings)
        model = settings.get("gemini_model", "gemini-2.5-flash")
        response = client.models.generate_content(model=model, contents=prompt)
        return jsonify({"result": response.text.strip()})
    except Exception as e:
        return jsonify({"result": text, "error": str(e)})


@app.route("/api/health", methods=["GET"])
def health():
    settings = load_settings()
    return jsonify({
        "status": "ok",
        "project": settings["gcp_project"],
        "table": build_full_table(settings),
    })


if __name__ == "__main__":
    # Migrate old single-table settings to multi-table format
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                old = json.load(f)
            if "bq_table" in old and "bq_tables" not in old:
                print("  [!] Migrating single-table settings to multi-table format...")
                old["bq_tables"] = [{
                    "table_name": old.pop("bq_table"),
                    "schema_description": old.pop("schema_description", "AUTO_DETECT")
                }]
                with open(SETTINGS_FILE, "w") as f:
                    json.dump(old, f, indent=2)
        except Exception:
            pass

    settings = load_settings()
    save_settings(settings)

    tables = settings.get("bq_tables", [])
    table_names = [t["table_name"] for t in tables]

    print("\n" + "=" * 55)
    print("   SATORI CAPABILITY INTELLIGENCE PLATFORM")
    print("=" * 55)
    print(f"   Project:  {settings['gcp_project']}")
    print(f"   Dataset:  {settings['bq_dataset']}")
    print(f"   Tables:   {', '.join(table_names)}")
    print(f"   Model:    {settings.get('gemini_model', 'gemini-2.5-flash')}")
    print(f"   State:    {state_store.mode}")
    print(f"   URL:      http://localhost:8080")
    print("=" * 55 + "\n")

    app.run(host="0.0.0.0", port=8080, debug=True)
