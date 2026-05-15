"""
Satori — Gemini + BigQuery service layer
=========================================
Pure functions extracted from the legacy app.py so FastAPI routers
can call them without inheriting any Flask coupling.
"""

import json
import math
import os
re_unused = None  # keep import slot for re below
import re
from datetime import datetime
from google import genai
from google.genai import types
from google.cloud import bigquery

# Re-import state store so functions can fetch conversation messages
from firestore_client import store as state_store
# These names are referenced inside ask_satori (extracted verbatim from
# the legacy app.py global scope). Bind them at module level so the
# function can call them without changes.
get_conversation_messages    = state_store.get_conversation_messages
add_message_to_conversation  = state_store.add_message_to_conversation

# Settings loader + secret resolution still live in main app; import lazily
# to avoid circular dependencies. These are referenced by ask_satori for
# language detection / pronunciation rules etc.
from secret_manager import get_secret
from config import DEFAULT_SETTINGS


# Cached clients — reset by settings handlers when keys change
_genai_client = None
_bq_client = None
_current_api_key = None

def reset_clients():
    """Force re-creation of clients on next access (after settings change)."""
    global _genai_client, _bq_client, _current_api_key
    _genai_client = None
    _bq_client = None
    _current_api_key = None

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
