"""
Satori — Configuration constants
=================================
Default settings, file paths. Imported by both legacy Flask app.py and
the new FastAPI main.py so they share one source of truth.
"""

import os

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
HISTORY_FILE  = os.path.join(BASE_DIR, "chat_history.json")

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
