import { useState, useMemo } from "react";
import { useAvailability, useDepartments, useStaffSuggestions } from "@/api/hooks";
import type { AvailabilityEmployee, StaffSuggestion } from "@/api/types";
import { Search, Plus, X, Sparkles, Briefcase, MapPin, AlertTriangle } from "lucide-react";
import clsx from "clsx";

const STATUS_CHIP: Record<string, string> = {
  Allocated: "border-indigo-300 bg-indigo-50 text-indigo-700",
  Partial:   "border-amber-300 bg-amber-50 text-amber-700",
  Bench:     "border-green-300 bg-green-50 text-green-700",
};

function initials(name: string) {
  return name.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
}

export default function AvailabilityPanel() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [showCreate, setShowCreate] = useState(false);

  const { data, isLoading, error } = useAvailability({ search, status });
  const employees = data?.employees ?? [];
  const summary = data?.summary;

  return (
    <div className="h-full flex flex-col">
      {/* KPI bar */}
      <div className="grid grid-cols-6 gap-3 px-6 py-4 border-b border-slate-200/80 dark:border-slate-800 bg-slate-50 dark:bg-satori-ink shrink-0">
        <Kpi label="Total Employees" value={summary?.total} accent="#7dc243" />
        <Kpi label="On Bench"          value={summary?.bench}        accent="#4ade80" sub="available now" />
        <Kpi label="Partial"           value={summary?.partial}      accent="#fbbf24" sub="some capacity" />
        <Kpi label="Allocated"         value={summary?.allocated}    accent="#818cf8" sub="no capacity" />
        <Kpi label="High Activity"     value={summary?.high_activity} accent="#2dd4bf" sub="120+ hrs / 90d" />
        <Kpi label="No Timesheet"      value={summary?.no_timesheet}  accent="#f87171" sub="0 hrs logged" />
      </div>

      {/* Toolbar */}
      <div className="px-6 py-3 border-b border-slate-200/80 dark:border-slate-800 flex items-center gap-2 shrink-0 bg-slate-50 dark:bg-satori-ink">
        <div className="flex-1 relative max-w-md">
          <Search className="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            className="input pl-8 w-full"
            placeholder="Search name, skill, position, location…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <select className="input" value={status} onChange={e => setStatus(e.target.value)}>
          <option value="">All Statuses</option>
          <option value="Bench">On Bench</option>
          <option value="Partial">Partially Available</option>
          <option value="Allocated">Allocated</option>
        </select>
        <button onClick={() => setShowCreate(true)} className="btn-primary">
          <Plus className="w-4 h-4" /> Create Task
        </button>
      </div>

      {/* Cards grid */}
      <div className="flex-1 overflow-y-auto p-6">
        {error ? (
          <div className="card p-4 text-sm text-red-400">Failed to load: {(error as Error).message}</div>
        ) : isLoading ? (
          <div className="text-center text-slate-500 py-12 text-sm">Loading availability data…</div>
        ) : employees.length === 0 ? (
          <div className="text-center text-slate-500 py-12 text-sm">No employees match your filters.</div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3.5">
            {employees.map(e => <EmployeeCard key={e.code} employee={e} />)}
          </div>
        )}
      </div>

      {showCreate && <CreateTaskModal onClose={() => setShowCreate(false)} />}
    </div>
  );
}

function Kpi({ label, value, accent, sub }: { label: string; value: number | undefined; accent: string; sub?: string }) {
  return (
    <div className="card p-3 border-t-2" style={{ borderTopColor: accent }}>
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="text-2xl font-bold leading-none mt-1" style={{ color: accent }}>
        {value?.toLocaleString() ?? "—"}
      </div>
      {sub && <div className="text-[10px] text-slate-500 mt-1">{sub}</div>}
    </div>
  );
}

function EmployeeCard({ employee: e }: { employee: AvailabilityEmployee }) {
  const skills = (e.competency || "").split(/[|,]/).map(s => s.trim()).filter(Boolean).slice(0, 3);
  const projs  = (e.proj_list || "").split(/,\s*/).filter(Boolean).slice(0, 2);
  const chip = STATUS_CHIP[e.status] ?? STATUS_CHIP.Bench;
  const tsColor =
    e.ts_label === "High Activity" ? "#2dd4bf" :
    e.ts_label === "Moderate Activity" ? "#7dc243" :
    e.ts_label === "Low Activity" ? "#fbbf24" : "#94a3b8";
  const pct = Math.max(0, Math.min(100, e.avg_allocation ?? 0));

  return (
    <div className="card card-hover p-4 flex flex-col gap-2.5">
      <div className="flex items-center gap-3">
        <div className={clsx("w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold border-2", chip)}>
          {initials(e.name)}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-semibold text-slate-900 truncate">{e.name}</div>
          <div className="text-[11px] text-slate-500 truncate">{e.position || e.hierarchy || "—"}</div>
        </div>
        <span className={clsx("pill text-[10px]", chip)}>{e.status}</span>
      </div>

      <div className="flex items-center gap-1 text-[10px] text-slate-500 -mt-1">
        <MapPin className="w-3 h-3" />
        <span className="truncate">{e.location}{e.hierarchy ? ` · ${e.hierarchy}` : ""}</span>
      </div>

      <div className="flex items-center gap-2" title="Average allocation %">
        <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
          <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: STATUS_CHIP[e.status]?.split(" ")[2] ?? "#7dc243" }} />
        </div>
        <span className="text-[11px] text-slate-500 font-mono min-w-[32px] text-right">{pct.toFixed(0)}%</span>
      </div>

      <div className="flex justify-between text-[10px] text-slate-500">
        <span>{e.proj_count} project{e.proj_count !== 1 ? "s" : ""}</span>
        <span style={{ color: tsColor }}>{e.ts_label} · {Math.round(e.ts_hours_90d)}h / 90d</span>
      </div>

      {skills.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {skills.map(s => <span key={s} className="pill bg-satori-green/10 text-satori-green text-[9px]">{s}</span>)}
        </div>
      )}
      {projs.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {projs.map(p => <span key={p} className="pill bg-blue-50 text-blue-700 text-[9px]">P-{p}</span>)}
        </div>
      )}
    </div>
  );
}

function CreateTaskModal({ onClose }: { onClose: () => void }) {
  const { data: deptData } = useDepartments();
  const departments = deptData?.departments ?? [];
  const staffMutation = useStaffSuggestions();

  const [form, setForm] = useState({ project_name: "", project_description: "", department: "", skills_required: "" });
  const [suggestions, setSuggestions] = useState<StaffSuggestion[] | null>(null);

  async function submit() {
    if (!form.project_name.trim() || !form.department) return;
    try {
      const resp = await staffMutation.mutateAsync(form);
      setSuggestions(resp.suggestions);
    } catch (e) {
      // surfaced via mutation.error below
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-6" onClick={onClose}>
      <div className="card max-w-2xl w-full max-h-[92vh] overflow-y-auto p-6 relative" onClick={e => e.stopPropagation()}>
        <button onClick={onClose} className="absolute top-3 right-3 text-slate-500 hover:text-slate-900">
          <X className="w-5 h-5" />
        </button>
        <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2 mb-1">
          <Sparkles className="w-5 h-5 text-satori-green" /> Create Task / Project
        </h2>
        <p className="text-xs text-slate-500 mb-5">
          Describe your project. Satori will scan the chosen department and recommend the best-fit employees
          based on availability, skills, and recent engagement.
        </p>

        {!suggestions ? (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Project name *">
                <input className="input w-full" value={form.project_name} onChange={e => setForm({ ...form, project_name: e.target.value })} placeholder="e.g. New CRM integration" />
              </Field>
              <Field label="Department *">
                <select className="input w-full" value={form.department} onChange={e => setForm({ ...form, department: e.target.value })}>
                  <option value="">Choose department…</option>
                  {departments.map(d => <option key={d.name} value={d.name}>{d.name} ({d.headcount})</option>)}
                </select>
              </Field>
            </div>
            <Field label="Project description">
              <textarea className="input w-full resize-none" rows={3} value={form.project_description}
                onChange={e => setForm({ ...form, project_description: e.target.value })}
                placeholder="What is the project about, target outcome, timeline…" />
            </Field>
            <Field label="Skills / keywords needed">
              <input className="input w-full" value={form.skills_required}
                onChange={e => setForm({ ...form, skills_required: e.target.value })}
                placeholder="e.g. Salesforce, Python, stakeholder management" />
            </Field>

            {staffMutation.isError && (
              <div className="card p-3 text-xs text-red-400 flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 mt-0.5" />
                <span>{(staffMutation.error as Error).message}</span>
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2">
              <button onClick={onClose} className="btn-ghost">Cancel</button>
              <button onClick={submit} disabled={staffMutation.isPending || !form.project_name.trim() || !form.department} className="btn-primary disabled:opacity-50">
                {staffMutation.isPending ? "Analysing…" : "Find Best Fit →"}
              </button>
            </div>
          </div>
        ) : (
          <SuggestionList suggestions={suggestions} onBack={() => setSuggestions(null)} projectName={form.project_name} />
        )}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  );
}

function SuggestionList({ suggestions, onBack, projectName }: { suggestions: StaffSuggestion[]; onBack: () => void; projectName: string }) {
  if (suggestions.length === 0) {
    return (
      <div>
        <div className="text-sm text-slate-500 mb-4">No suitable candidates found in that department.</div>
        <button onClick={onBack} className="btn-ghost">← Try a different brief</button>
      </div>
    );
  }
  return (
    <div>
      <h3 className="text-sm font-semibold text-slate-800 mb-3">
        Top {suggestions.length} fits for <span className="text-satori-green">{projectName}</span>
      </h3>
      <div className="space-y-2.5">
        {suggestions.map((s, idx) => {
          const fit = Math.max(0, Math.min(100, s.fit_score));
          const fitColor = fit >= 80 ? "#4ade80" : fit >= 60 ? "#7dc243" : fit >= 40 ? "#fbbf24" : "#ef4444";
          const skills = (s.competencies || "").split(/[|,]/).map(x => x.trim()).filter(Boolean).slice(0, 4);
          return (
            <div key={idx} className="card p-3.5 flex gap-3">
              <div className="relative shrink-0">
                <div className="w-10 h-10 rounded-full border-2 border-slate-300 bg-slate-100 flex items-center justify-center text-xs font-bold text-slate-800">
                  {initials(s.name)}
                </div>
                <span className={clsx(
                  "absolute -top-1.5 -left-1.5 w-4 h-4 rounded-full text-[9px] font-bold flex items-center justify-center text-white",
                  idx === 0 ? "bg-amber-500" : idx === 1 ? "bg-slate-400" : idx === 2 ? "bg-amber-700" : "bg-slate-600",
                )}>{idx + 1}</span>
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-slate-900 truncate">{s.name}</div>
                    <div className="text-[11px] text-slate-500 truncate">{s.position} · {s.location}</div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="text-base font-bold leading-none" style={{ color: fitColor }}>{fit}<span className="text-[10px] opacity-70">/100</span></div>
                    <div className="text-[9px] text-slate-500 uppercase tracking-wider">Fit Score</div>
                  </div>
                </div>
                {s.headline && <div className="mt-1.5 text-xs font-medium" style={{ color: fitColor }}>{s.headline}</div>}
                <p className="text-[11px] text-slate-500 leading-relaxed mt-1.5">{s.reasoning}</p>
                {s.caveat && (
                  <div className="mt-1.5 text-[10px] text-amber-300 bg-amber-500/10 border-l-2 border-amber-500 px-2 py-1 rounded">
                    <strong>Caveat:</strong> {s.caveat}
                  </div>
                )}
                <div className="flex flex-wrap gap-1.5 mt-2 text-[10px] text-slate-500">
                  <span className="px-2 py-0.5 rounded-full bg-slate-100">Allocation: <strong>{s.avg_allocation.toFixed(0)}%</strong></span>
                  <span className="px-2 py-0.5 rounded-full bg-slate-100">Free: <strong className="text-green-300">{s.free_capacity.toFixed(0)}%</strong></span>
                  <span className="px-2 py-0.5 rounded-full bg-slate-100">{s.active_projects} active</span>
                  <span className="px-2 py-0.5 rounded-full bg-slate-100">{Math.round(s.ts_hours_90d)}h / 90d</span>
                </div>
                {skills.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1.5">
                    {skills.map(sk => <span key={sk} className="pill bg-satori-green/10 text-satori-green text-[9px]">{sk}</span>)}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
      <div className="flex justify-end mt-4">
        <button onClick={onBack} className="btn-ghost text-xs">← Try a different brief</button>
      </div>
    </div>
  );
}
