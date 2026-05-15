import { useMemo, useState } from "react";
import { useCapabilityMatrix } from "@/api/hooks";
import type { MatrixEmployee } from "@/api/types";
import { StarRow } from "@/components/StarRow";
import { Search, Filter, X, Briefcase, Clock, MapPin } from "lucide-react";
import clsx from "clsx";

const STATUS_RING: Record<string, string> = {
  Allocated: "border-indigo-300 bg-indigo-50 text-indigo-700",
  Partial:   "border-amber-300 bg-amber-50 text-amber-700",
  Bench:     "border-green-300 bg-green-50 text-green-700",
};

function initials(name: string) {
  return name.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
}

function avgStars(stars: Record<string, number> | undefined) {
  const vals = Object.values(stars ?? {});
  if (!vals.length) return 0;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

export default function CapabilityMatrixPanel() {
  const { data, isLoading, error } = useCapabilityMatrix();
  const [search, setSearch] = useState("");
  const [skillFilter, setSkillFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [minStar, setMinStar] = useState(0);
  const [sortBy, setSortBy] = useState<"stars" | "hours" | "projects" | "attendance" | "name">("stars");
  const [selected, setSelected] = useState<MatrixEmployee | null>(null);

  const employees = data?.employees ?? [];
  const categories = data?.categories ?? [];
  const topSkills = data?.top_skills ?? [];
  const summary = data?.summary ?? ({} as Record<string, number>);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const skill = skillFilter.toLowerCase();
    return employees.filter(e => {
      if (q) {
        const blob = `${e.name} ${e.competencies} ${e.position} ${e.location} ${e.project_list} ${e.ts_projects} ${e.hierarchy}`.toLowerCase();
        if (!blob.includes(q)) return false;
      }
      if (skill && !(e.competencies || "").toLowerCase().includes(skill)) return false;
      if (statusFilter && e.alloc_status !== statusFilter) return false;
      if (minStar > 0 && avgStars(e.stars) < minStar) return false;
      return true;
    });
  }, [employees, search, skillFilter, statusFilter, minStar]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      switch (sortBy) {
        case "stars":      return avgStars(b.stars) - avgStars(a.stars);
        case "hours":      return (b.ts_hours_90d || 0) - (a.ts_hours_90d || 0);
        case "projects":   return (b.project_count || 0) - (a.project_count || 0);
        case "attendance": return (b.presence_rate || 0) - (a.presence_rate || 0);
        case "name":       return a.name.localeCompare(b.name);
      }
    });
    return arr;
  }, [filtered, sortBy]);

  if (error) {
    return (
      <div className="p-6">
        <div className="card p-5 border-red-700/40 text-sm text-red-300">
          <strong className="block mb-1">Failed to load capability matrix</strong>
          {(error as Error).message}
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      {/* KPI bar */}
      <div className="grid grid-cols-4 gap-3 px-6 py-4 border-b border-slate-200 bg-slate-50 shrink-0">
        <div className="kpi">
          <div className="text-[10px] uppercase tracking-wider text-slate-500">Active Employees</div>
          <div className="text-2xl font-bold text-satori-green leading-none mt-1">
            {(summary.total ?? employees.length).toLocaleString()}
          </div>
        </div>
        {categories.slice(0, 2).map((cat, i) => (
          <div key={cat.id} className={clsx("kpi", i === 0 ? "border-t-amber-400" : "border-t-indigo-400")}>
            <div className="text-[10px] uppercase tracking-wider text-slate-500 truncate" title={cat.name}>{cat.name}</div>
            <div className="text-2xl font-bold leading-none mt-1">
              ★ {(summary[`avg_stars_${cat.id}`] ?? 0).toFixed(1)}
            </div>
            <div className="text-[10px] text-slate-500 mt-1">average across active employees</div>
          </div>
        ))}
        <div className="kpi border-t-emerald-400">
          <div className="text-[10px] uppercase tracking-wider text-slate-500">Top Performer</div>
          <div className="text-sm font-semibold text-slate-900 mt-1 leading-tight truncate">
            {sorted[0]?.name.split(" ").slice(0, 2).join(" ") ?? "—"}
          </div>
          {sorted[0] && <StarRow rating={avgStars(sorted[0].stars)} />}
        </div>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-2 px-6 py-3 border-b border-slate-200 bg-slate-50 shrink-0">
        <div className="relative flex-1 min-w-[200px] max-w-md">
          <Search className="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            className="input pl-8 w-full"
            placeholder="Search name, skill, position, location…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <select className="input" value={skillFilter} onChange={e => setSkillFilter(e.target.value)}>
          <option value="">All Skills</option>
          {topSkills.slice(0, 30).map(sk => (
            <option key={sk.skill} value={sk.skill}>{sk.skill} ({sk.count})</option>
          ))}
        </select>
        <select className="input" value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
          <option value="">All Statuses</option>
          <option value="Allocated">Allocated</option>
          <option value="Partial">Partial</option>
          <option value="Bench">On Bench</option>
        </select>
        <select className="input" value={minStar} onChange={e => setMinStar(parseFloat(e.target.value))}>
          <option value="0">Any Rating</option>
          <option value="4">≥ 4 ★</option>
          <option value="3">≥ 3 ★</option>
          <option value="2">≥ 2 ★</option>
        </select>
        <select className="input" value={sortBy} onChange={e => setSortBy(e.target.value as any)}>
          <option value="stars">Top Rated</option>
          <option value="hours">Most Hours</option>
          <option value="projects">Most Projects</option>
          <option value="attendance">Best Attendance</option>
          <option value="name">Name A–Z</option>
        </select>
        <div className="ml-auto text-xs text-slate-500">
          {sorted.length.toLocaleString()} employees
        </div>
      </div>

      {/* Skill cloud */}
      {topSkills.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-6 py-3 border-b border-slate-200 shrink-0">
          {topSkills.slice(0, 24).map(sk => (
            <button
              key={sk.skill}
              onClick={() => setSkillFilter(sk.skill)}
              className={clsx(
                "px-2.5 py-1 rounded-full text-[11px] border transition",
                skillFilter === sk.skill
                  ? "border-satori-green bg-satori-green/10 text-satori-green"
                  : "border-slate-300 bg-white text-slate-500 hover:border-slate-500 hover:text-slate-800",
              )}
            >
              {sk.skill} <span className="opacity-50 ml-0.5">{sk.count}</span>
            </button>
          ))}
        </div>
      )}

      {/* Cards grid */}
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <div className="text-center text-slate-500 py-12 text-sm">Loading workforce intelligence…</div>
        ) : sorted.length === 0 ? (
          <div className="text-center text-slate-500 py-12 text-sm">No employees match the current filters.</div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3.5">
            {sorted.map((e, idx) => {
              const stars = e.stars ?? {};
              const avg = avgStars(stars);
              const ring = STATUS_RING[e.alloc_status] ?? STATUS_RING.Bench;
              const skills = (e.competencies || "").split(/[|,]/).map(s => s.trim()).filter(Boolean).slice(0, 4);

              return (
                <button
                  key={e.code + idx}
                  onClick={() => setSelected(e)}
                  className="card card-hover p-4 text-left flex flex-col gap-2.5"
                >
                  <div className="flex items-center gap-3">
                    <div className={clsx("relative w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold border-2", ring)}>
                      {initials(e.name)}
                      {idx < 3 && (
                        <span className={clsx(
                          "absolute -top-1 -left-1 w-4 h-4 rounded-full text-[9px] font-bold flex items-center justify-center text-white",
                          idx === 0 ? "bg-amber-500" : idx === 1 ? "bg-slate-400" : "bg-amber-700",
                        )}>{idx + 1}</span>
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="font-semibold text-sm text-slate-900 truncate">{e.name}</div>
                      <div className="text-[11px] text-slate-500 truncate">{e.position || e.hierarchy || "—"}</div>
                    </div>
                    <span className={clsx("pill", ring)}>{e.alloc_status}</span>
                  </div>

                  <div className="flex items-center gap-1.5 text-[10px] text-slate-500">
                    <MapPin className="w-3 h-3" />
                    <span className="truncate">{e.location || "—"}</span>
                  </div>

                  {/* Category stars */}
                  <div className="space-y-1">
                    {(data?.categories ?? []).map(cat => (
                      <div key={cat.id} className="flex items-center justify-between text-[10px]">
                        <span className="text-slate-500 truncate">{cat.name}</span>
                        <StarRow rating={stars[cat.id] ?? 0} showNumber={false} />
                      </div>
                    ))}
                  </div>

                  {/* Footer stats */}
                  <div className="flex items-center justify-between pt-2 border-t border-slate-200 text-[10px] text-slate-500">
                    <span className="flex items-center gap-1"><Briefcase className="w-3 h-3" /> {e.project_count} proj</span>
                    <span className="flex items-center gap-1"><Clock className="w-3 h-3" /> {Math.round(e.ts_hours_90d || 0)}h</span>
                    <span className="font-mono text-amber-400">★ {avg.toFixed(1)}</span>
                  </div>

                  {skills.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {skills.map(s => (
                        <span key={s} className="pill bg-slate-100 text-slate-500 text-[9px]">{s}</span>
                      ))}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Detail modal */}
      {selected && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-6" onClick={() => setSelected(null)}>
          <div className="card max-w-2xl w-full max-h-[88vh] overflow-y-auto p-6 relative" onClick={e => e.stopPropagation()}>
            <button onClick={() => setSelected(null)} className="absolute top-3 right-3 text-slate-500 hover:text-slate-900">
              <X className="w-5 h-5" />
            </button>
            <div className="flex items-center gap-4 mb-4">
              <div className={clsx("w-14 h-14 rounded-full flex items-center justify-center text-lg font-bold border-2", STATUS_RING[selected.alloc_status] ?? STATUS_RING.Bench)}>
                {initials(selected.name)}
              </div>
              <div className="flex-1">
                <h2 className="text-lg font-semibold text-slate-900">{selected.name}</h2>
                <div className="text-xs text-slate-500">{selected.code} · {selected.position} · {selected.location}</div>
              </div>
              <span className={clsx("pill", STATUS_RING[selected.alloc_status])}>{selected.alloc_status}</span>
            </div>

            <div className="grid grid-cols-2 gap-3 mb-4">
              {(data?.categories ?? []).map(cat => (
                <div key={cat.id} className="card p-3">
                  <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">{cat.name}</div>
                  <StarRow rating={selected.stars?.[cat.id] ?? 0} />
                  <div className="text-[10px] text-slate-500 mt-2 leading-relaxed">{cat.reasoning}</div>
                </div>
              ))}
            </div>

            <div className="grid grid-cols-3 gap-3 mb-4">
              <Stat label="Active Projects" value={selected.project_count.toString()} />
              <Stat label="Hours (90d)" value={`${Math.round(selected.ts_hours_90d || 0)}h`} />
              <Stat label="Presence Rate" value={`${(selected.presence_rate || 0).toFixed(1)}%`} />
            </div>

            <Detail label="Competencies"  value={selected.competencies || "—"} />
            <Detail label="Project list"  value={selected.project_list || "—"} />
            <Detail label="Timesheet projects" value={selected.ts_projects || "—"} />
            <Detail label="Hierarchy"     value={selected.hierarchy || "—"} />
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card p-3">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="text-base font-bold text-slate-900 mt-1">{value}</div>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-sm mb-3">
      <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">{label}</div>
      <div className="text-slate-700 leading-relaxed break-words">{value}</div>
    </div>
  );
}
