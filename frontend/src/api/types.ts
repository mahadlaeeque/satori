/**
 * TypeScript types for Satori's FastAPI responses.
 * Mirrors the shapes returned by main.py routers.
 */

// ── Chat / history ────────────────────────────────────────────────────
export interface ChatMessage {
  role: "user" | "bot";
  content: string;
  timestamp?: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface AskResponse {
  answer: string;
  conversation_id: string;
  sql?: string;
  sources?: Array<{ table: string; rows?: number }>;
  error?: boolean;
  route?: string;
}

// ── Availability ──────────────────────────────────────────────────────
export interface AvailabilityEmployee {
  name: string;
  code: string;
  position: string;
  location: string;
  hierarchy: string;
  employee_type: string;
  employee_status: string;
  competency: string;
  avg_allocation: number;
  max_allocation: number;
  proj_count: number;
  proj_list: string;
  flag: string;
  actual_weeks: number;
  forecast_weeks: number;
  last_alloc_date: string;
  ts_hours_90d: number;
  ts_label: string;
  ts_projects: string;
  last_ts_date: string;
  status: "Allocated" | "Partial" | "Bench";
}

export interface AvailabilityResponse {
  employees: AvailabilityEmployee[];
  summary: {
    total: number;
    allocated: number;
    partial: number;
    bench: number;
    high_activity: number;
    no_timesheet: number;
  };
  top_competencies: Array<{ skill: string; count: number }>;
}

// ── Predictive ────────────────────────────────────────────────────────
export interface PredictiveEmployee {
  name: string;
  overall_rate: number;
  recent_4w_rate: number;
  prev_4w_rate: number;
  delta: number;
  total_absent: number;
  total_late: number;
  total_days: number;
  weeks_recorded: number;
  risk: "High" | "Medium" | "Low";
  sparkline: Array<{ week: string; rate: number }>;
}

export interface PredictiveResponse {
  employees: PredictiveEmployee[];
  summary: {
    total: number;
    high_risk: number;
    medium_risk: number;
    low_risk: number;
    avg_rate: number;
  };
  org_trend: Array<{ week: string; rate: number }>;
}

// ── Capability matrix ────────────────────────────────────────────────
export interface MatrixCategory {
  id: string;
  name: string;
  weight: number;
  formula?: string;
  reasoning?: string;
}

export interface MatrixEmployee {
  name: string;
  code: string;
  position: string;
  location: string;
  hierarchy: string;
  competencies: string;
  project_list: string;
  ts_projects: string;
  project_count: number;
  ts_hours_90d: number;
  presence_rate: number;
  alloc_status: "Allocated" | "Partial" | "Bench";
  stars: Record<string, number>;
}

export interface CapabilityMatrixResponse {
  employees: MatrixEmployee[];
  summary: Record<string, number>;
  categories: MatrixCategory[];
  top_skills: Array<{ skill: string; count: number }>;
}

// ── AI staffing suggestions ──────────────────────────────────────────
export interface StaffSuggestion {
  name: string;
  code: string;
  position: string;
  location: string;
  department: string;
  avg_allocation: number;
  free_capacity: number;
  active_projects: number;
  competencies: string;
  ts_hours_90d: number;
  fit_score: number;
  headline: string;
  reasoning: string;
  caveat: string;
}

export interface StaffSuggestionsResponse {
  suggestions: StaffSuggestion[];
  total_candidates: number;
  department: string;
  project_name: string;
  message?: string;
}

// ── Employee pattern ─────────────────────────────────────────────────
export interface WeekdayStat {
  weekday: string;
  total: number;
  present: number;
  absent: number;
  late: number;
  rate: number;
  late_pct: number;
}

export interface EmployeePattern {
  name: string;
  weeks_tracked: number;
  overall_rate: number;
  recent_4w_rate: number;
  prev_4w_rate: number;
  delta: number;
  projected_next_week: number;
  risk: "High" | "Medium" | "Low";
  total_absent: number;
  total_late: number;
  avg_checkin: string;
  checkin_std_min: number;
  weekdays: WeekdayStat[];
  worst_weekday: string | null;
  weekly: Array<{ week: string; rate: number; absences: number; lates: number }>;
  recent_absences: Array<{ date: string; status: string }>;
  problems: string[];
}

export interface ProductivityTip {
  summary: string;
  root_cause_hypothesis?: string;
  tips: string[];
  manager_action?: string;
  tone: "supportive" | "cautionary" | "celebratory";
  error?: string;
}

// ── Departments ───────────────────────────────────────────────────────
export interface Department {
  name: string;
  headcount: number;
}

// ── Attendance analytics ─────────────────────────────────────────────
export interface AttendanceSummary {
  total_records: number;
  unique_employees: number;
  present_days: number;
  absent_days: number;
  leave_days: number;
  remote_days: number;
  holiday_days: number;
  weekend_days: number;
  late_count: number;
  attendance_rate: number;
  late_rate: number;
  on_time_rate: number;
}

export interface AttendanceTrendPoint {
  date: string;
  total: number;
  present: number;
  absent: number;
  late: number;
  rate: number;
}

export interface AttendanceDoW {
  weekday: string;
  rate: number;
  late_pct: number;
  present: number;
  late: number;
}

export interface AttendanceCheckinBucket {
  hour: number;
  count: number;
}

export interface AttendanceDept {
  department: string;
  employees: number;
  rate: number;
  late: number;
  total: number;
}

export interface AttendanceInsight {
  severity: "good" | "warning" | "info";
  title: string;
  body: string;
}

export interface AttendanceAnalyticsResponse {
  summary: AttendanceSummary;
  daily_trend: AttendanceTrendPoint[];
  day_of_week: AttendanceDoW[];
  checkin_dist: AttendanceCheckinBucket[];
  dept_breakdown: AttendanceDept[];
  top_absent: Array<{ name: string; absent_days: number }>;
  top_late: Array<{ name: string; late_count: number }>;
  insights: AttendanceInsight[];
  filters: {
    range: number;
    date_from: string;
    date_to: string;
    department: string;
    employee: string;
  };
}
