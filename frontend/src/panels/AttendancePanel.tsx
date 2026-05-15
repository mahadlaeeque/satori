import { useMemo, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer,
  Tooltip, XAxis, YAxis, Legend,
} from "recharts";
import {
  AlertTriangle, CalendarRange, ChevronDown, Clock, Search, Sparkles,
  TrendingDown, TrendingUp, UserMinus,
} from "lucide-react";
import clsx from "clsx";
import { useAttendanceAnalytics, useDepartments } from "@/api/hooks";

type RangeKey = "7" | "30" | "90" | "180";
const RANGE_OPTIONS: Array<{ key: RangeKey; label: string }> = [
  { key: "7",   label: "Last 7 days"   },
  { key: "30",  label: "Last 30 days"  },
  { key: "90",  label: "Last 90 days"  },
  { key: "180", label: "Last 180 days" },
];

const SATORI_GREEN = "#7dc243";
const SATORI_TEAL  = "#0a9396";
const AMBER        = "#fbbf24";
const RED          = "#f87171";
const SLATE_400    = "#94a3b8";

export default function AttendancePanel() {
  const [range,       setRange]       = useState<RangeKey>("30");
  const [department,  setDepartment]  = useState<string>("");
  const [employee,    setEmployee]    = useState<string>("");
  const [employeeDraft, setEmployeeDraft] = useState<string>("");

  const { data, isLoading, error, isFetching } = useAttendanceAnalytics({
    range: Number(range),
    department: department || undefined,
    employee:   employee   || undefined,
  });
  const { data: deptsData } = useDepartments();

  const summary       = data?.summary;
  const trend         = data?.daily_trend  ?? [];
  const dow           = data?.day_of_week  ?? [];
  const checkinDist   = data?.checkin_dist ?? [];
  const deptBreakdown = data?.dept_breakdown ?? [];
  const topAbsent     = data?.top_absent   ?? [];
  const topLate       = data?.top_late     ?? [];
  const insights      = data?.insights     ?? [];

  // Pre-format weekday labels for clearer x-axis ticks
  const dowFormatted = useMemo(() =>
    dow.map(d => ({ ...d, label: d.weekday.slice(0, 3) })),
  [dow]);

  // Group hours into morning/afternoon/etc buckets isn't necessary — the
  // raw distribution is itself informative. We just zero-fill missing hours.
  const hourSeries = useMemo(() => {
    const map = new Map(checkinDist.map(b => [b.hour, b.count]));
    const minH = Math.min(...checkinDist.map(b => b.hour), 6);
    const maxH = Math.max(...checkinDist.map(b => b.hour), 18);
    const out = [];
    for (let h = Math.max(0, minH - 1); h <= Math.min(23, maxH + 1); h++) {
      out.push({ hour: h, label: `${String(h).padStart(2,"0")}:00`, count: map.get(h) ?? 0 });
    }
    return out;
  }, [checkinDist]);

  function applyEmployee() {
    setEmployee(employeeDraft.trim());
  }
  function clearFilters() {
    setRange("30"); setDepartment(""); setEmployee(""); setEmployeeDraft("");
  }

  return (
    <div className="h-full flex flex-col">
      {/* ── Filter bar ──────────────────────────────────────────────── */}
      <div className="px-6 py-3 border-b border-slate-200/80 dark:border-slate-800 flex flex-wrap items-center gap-3 shrink-0 bg-slate-50 dark:bg-satori-ink">
        <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
          <CalendarRange className="w-4 h-4" />
          <span className="font-medium">Range</span>
        </div>
        <div className="flex gap-1">
          {RANGE_OPTIONS.map(opt => (
            <button
              key={opt.key}
              onClick={() => setRange(opt.key)}
              className={clsx(
                "px-3 py-1.5 text-xs font-medium rounded-md border transition",
                range === opt.key
                  ? "bg-satori-green text-white border-satori-green"
                  : "bg-white dark:bg-satori-paper border-slate-200 dark:border-slate-800 text-slate-600 dark:text-slate-300 hover:border-satori-green/40",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <div className="relative">
          <select
            value={department}
            onChange={e => setDepartment(e.target.value)}
            className="input pr-8 appearance-none cursor-pointer"
            title="Filter by department"
          >
            <option value="">All departments</option>
            {(deptsData?.departments ?? []).map(d => (
              <option key={d.name} value={d.name}>{d.name} ({d.headcount})</option>
            ))}
          </select>
          <ChevronDown className="w-3.5 h-3.5 absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-slate-400" />
        </div>

        <div className="relative flex-1 max-w-xs">
          <Search className="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 dark:text-slate-500" />
          <input
            value={employeeDraft}
            onChange={e => setEmployeeDraft(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") applyEmployee(); }}
            onBlur={applyEmployee}
            placeholder="Filter by employee name…"
            className="input pl-8 w-full"
          />
        </div>

        {(department || employee) && (
          <button onClick={clearFilters} className="btn-ghost text-xs">Clear filters</button>
        )}

        <div className="ml-auto text-xs text-slate-500 dark:text-slate-400">
          {isFetching ? "Refreshing…" : summary && `${summary.total_records.toLocaleString()} records · ${summary.unique_employees.toLocaleString()} employees`}
        </div>
      </div>

      {/* ── Body ────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-auto p-6 bg-slate-50 dark:bg-satori-ink space-y-6">
        {error ? (
          <div className="card p-4 text-sm text-red-500">Failed to load: {(error as Error).message}</div>
        ) : isLoading || !summary ? (
          <div className="text-center text-slate-500 dark:text-slate-400 py-16 text-sm">Crunching attendance numbers…</div>
        ) : (
          <>
            {/* KPI tiles */}
            <KpiRow s={summary} />

            {/* Charts grid */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
              <Card title="Daily Attendance Rate" subtitle="% present per day across the selected window">
                <ResponsiveContainer width="100%" height={240}>
                  <AreaChart data={trend} margin={{ top: 4, right: 16, left: -10, bottom: 0 }}>
                    <defs>
                      <linearGradient id="rateGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%"  stopColor={SATORI_GREEN} stopOpacity={0.7} />
                        <stop offset="100%" stopColor={SATORI_GREEN} stopOpacity={0.05} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="#E2E8F0" strokeDasharray="3 3" vertical={false} className="dark:opacity-20" />
                    <XAxis dataKey="date" tick={{ fontSize: 10, fill: SLATE_400 }} interval="preserveStartEnd" minTickGap={28} />
                    <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: SLATE_400 }} unit="%" />
                    <Tooltip content={<RechartsTip suffix="%" />} />
                    <Area type="monotone" dataKey="rate" stroke={SATORI_GREEN} strokeWidth={2} fill="url(#rateGrad)" />
                  </AreaChart>
                </ResponsiveContainer>
              </Card>

              <Card title="Department Breakdown" subtitle="Attendance rate by department (top 12 by headcount)">
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart
                    data={deptBreakdown}
                    layout="vertical"
                    margin={{ top: 4, right: 24, left: 8, bottom: 0 }}
                  >
                    <CartesianGrid stroke="#E2E8F0" strokeDasharray="3 3" horizontal={false} className="dark:opacity-20" />
                    <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 10, fill: SLATE_400 }} unit="%" />
                    <YAxis dataKey="department" type="category" tick={{ fontSize: 10, fill: SLATE_400 }} width={130} interval={0} />
                    <Tooltip content={<RechartsTip suffix="%" />} />
                    <Bar dataKey="rate" radius={[0, 4, 4, 0]}>
                      {deptBreakdown.map((d, i) => (
                        <Cell key={i} fill={d.rate >= 90 ? SATORI_GREEN : d.rate >= 80 ? AMBER : RED} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </Card>

              <Card title="Day-of-Week Patterns" subtitle="Attendance vs. late-arrival rate per weekday (excludes weekends/holidays)">
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={dowFormatted} margin={{ top: 4, right: 16, left: -10, bottom: 0 }}>
                    <CartesianGrid stroke="#E2E8F0" strokeDasharray="3 3" vertical={false} className="dark:opacity-20" />
                    <XAxis dataKey="label" tick={{ fontSize: 11, fill: SLATE_400 }} />
                    <YAxis yAxisId="left"  domain={[0, 100]} tick={{ fontSize: 10, fill: SLATE_400 }} unit="%" />
                    <YAxis yAxisId="right" orientation="right" domain={[0, 30]}  tick={{ fontSize: 10, fill: SLATE_400 }} unit="%" />
                    <Tooltip content={<RechartsTip />} />
                    <Legend iconSize={10} wrapperStyle={{ fontSize: 11 }} />
                    <Bar yAxisId="left"  dataKey="rate"     name="Attendance %" fill={SATORI_TEAL} radius={[4,4,0,0]} />
                    <Bar yAxisId="right" dataKey="late_pct" name="Late %"       fill={AMBER}        radius={[4,4,0,0]} />
                  </BarChart>
                </ResponsiveContainer>
              </Card>

              <Card title="Check-in Distribution" subtitle="Hour of day when present employees check in">
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={hourSeries} margin={{ top: 4, right: 16, left: -10, bottom: 0 }}>
                    <CartesianGrid stroke="#E2E8F0" strokeDasharray="3 3" vertical={false} className="dark:opacity-20" />
                    <XAxis dataKey="label" tick={{ fontSize: 10, fill: SLATE_400 }} interval={1} />
                    <YAxis tick={{ fontSize: 10, fill: SLATE_400 }} />
                    <Tooltip content={<RechartsTip />} />
                    <Bar dataKey="count" name="Check-ins" radius={[4,4,0,0]}>
                      {hourSeries.map((b, i) => (
                        <Cell key={i} fill={b.hour <= 9 ? SATORI_GREEN : b.hour <= 10 ? AMBER : RED} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </Card>
            </div>

            {/* Lower row — lists + insights */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
              <Card title="Top Absentees" subtitle="Most absent in window" icon={<UserMinus className="w-4 h-4 text-red-500" />}>
                <RankList
                  items={topAbsent.map(t => ({ label: t.name, value: t.absent_days, suffix: "days" }))}
                  color={RED}
                />
              </Card>
              <Card title="Top Late Arrivals" subtitle="Highest late count in window" icon={<Clock className="w-4 h-4 text-amber-500" />}>
                <RankList
                  items={topLate.map(t => ({ label: t.name, value: t.late_count, suffix: "× late" }))}
                  color={AMBER}
                />
              </Card>
              <Card title="Insights" subtitle="Auto-generated from the selected window" icon={<Sparkles className="w-4 h-4 text-satori-green" />}>
                {insights.length === 0 ? (
                  <div className="text-xs text-slate-500 dark:text-slate-400 px-1 py-2">No notable patterns in this slice.</div>
                ) : (
                  <ul className="space-y-2.5">
                    {insights.map((it, i) => (
                      <li key={i} className="flex gap-2.5 text-xs">
                        <InsightIcon severity={it.severity} />
                        <div>
                          <div className="font-semibold text-slate-800 dark:text-slate-100">{it.title}</div>
                          <div className="text-slate-600 dark:text-slate-300 leading-relaxed">{it.body}</div>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────
   Sub-components
   ───────────────────────────────────────────────────────────────────── */

function KpiRow({ s }: { s: import("@/api/types").AttendanceSummary }) {
  const tiles = [
    { label: "Attendance rate", value: `${s.attendance_rate}%`, accent: "#7dc243",
      footnote: `${s.present_days.toLocaleString()} present days` },
    { label: "On-time rate", value: `${s.on_time_rate}%`, accent: "#0a9396",
      footnote: `${s.late_count.toLocaleString()} late arrivals` },
    { label: "Total absences", value: s.absent_days.toLocaleString(), accent: "#f87171",
      footnote: `${s.unique_employees.toLocaleString()} employees in scope` },
    { label: "On leave", value: s.leave_days.toLocaleString(), accent: "#fbbf24",
      footnote: `${s.remote_days.toLocaleString()} remote days` },
    { label: "Late arrivals", value: s.late_count.toLocaleString(), accent: "#a855f7",
      footnote: `${s.late_rate}% of present` },
    { label: "Records", value: s.total_records.toLocaleString(), accent: "#94a3b8",
      footnote: `${s.holiday_days.toLocaleString()} holiday / ${s.weekend_days.toLocaleString()} weekend` },
  ];
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      {tiles.map(t => (
        <div key={t.label} className="card p-3 border-t-2" style={{ borderTopColor: t.accent }}>
          <div className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-400">{t.label}</div>
          <div className="text-2xl font-bold leading-none mt-1 num text-slate-900 dark:text-slate-100" style={{ color: t.accent }}>
            {t.value}
          </div>
          <div className="text-[10px] text-slate-500 dark:text-slate-400 mt-1.5">{t.footnote}</div>
        </div>
      ))}
    </div>
  );
}

function Card(
  { title, subtitle, icon, children }:
  { title: string; subtitle?: string; icon?: React.ReactNode; children: React.ReactNode }
) {
  return (
    <div className="card p-4">
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-800 dark:text-slate-100">
            {icon}
            <span>{title}</span>
          </div>
          {subtitle && (
            <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">{subtitle}</div>
          )}
        </div>
      </div>
      {children}
    </div>
  );
}

function RankList(
  { items, color }:
  { items: Array<{ label: string; value: number; suffix?: string }>; color: string }
) {
  if (items.length === 0) {
    return <div className="text-xs text-slate-500 dark:text-slate-400 px-1 py-2">Nothing to report.</div>;
  }
  const max = Math.max(...items.map(i => i.value), 1);
  return (
    <ul className="space-y-1.5">
      {items.map((it, i) => (
        <li key={i} className="text-xs">
          <div className="flex items-center justify-between">
            <span className="truncate text-slate-700 dark:text-slate-200 font-medium">{it.label}</span>
            <span className="num text-slate-500 dark:text-slate-400 ml-2 shrink-0">
              {it.value} <span className="text-[10px]">{it.suffix}</span>
            </span>
          </div>
          <div className="h-1.5 bg-slate-100 dark:bg-slate-800 rounded-full mt-1 overflow-hidden">
            <div className="h-full rounded-full" style={{ width: `${(it.value / max) * 100}%`, backgroundColor: color }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function InsightIcon({ severity }: { severity: "good" | "warning" | "info" }) {
  if (severity === "good")    return <TrendingUp className="w-4 h-4 text-satori-green shrink-0 mt-0.5" />;
  if (severity === "warning") return <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />;
  return <TrendingDown className="w-4 h-4 text-slate-400 shrink-0 mt-0.5" />;
}

/** Recharts default tooltip is fine but doesn't match the rest of the UI.
 * This one stays compact, themed, and respects dark mode. */
function RechartsTip(
  { active, payload, label, suffix = "" }:
  { active?: boolean; payload?: any[]; label?: string; suffix?: string }
) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md px-2.5 py-1.5 text-[11px] shadow-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900">
      {label && <div className="font-semibold text-slate-800 dark:text-slate-100 mb-0.5">{label}</div>}
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 text-slate-600 dark:text-slate-300">
          <span className="w-2 h-2 rounded-sm" style={{ backgroundColor: p.color || p.fill }} />
          <span>{p.name}: <span className="num font-medium text-slate-800 dark:text-slate-100">{p.value}{suffix}</span></span>
        </div>
      ))}
    </div>
  );
}
