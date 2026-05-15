import { NavLink } from "react-router-dom";
import {
  MessageCircle, Grid3X3, Users, Calendar, Clock,
  Building2, BarChart3, TrendingUp, ListChecks, Settings as Cog,
} from "lucide-react";
import clsx from "clsx";

interface NavItem  { to: string; label: string; icon: typeof MessageCircle; }
interface NavGroup { heading: string; items: NavItem[]; }

const GROUPS: NavGroup[] = [
  {
    heading: "AI Tools",
    items: [{ to: "/chat", label: "Ask Me Anything", icon: MessageCircle }],
  },
  {
    heading: "Workforce Data",
    items: [
      { to: "/attendance", label: "Attendance",          icon: Calendar },
      { to: "/timesheet",  label: "Timesheets",          icon: Clock },
      { to: "/resources",  label: "Resource Allocation", icon: Users },
      { to: "/employees",  label: "Employee Data",       icon: Users },
    ],
  },
  {
    heading: "Sales Data",
    items: [
      { to: "/coverage",     label: "Account Coverage", icon: Building2 },
      { to: "/pipeline",     label: "Pipeline Health",  icon: TrendingUp },
      { to: "/amscorecard",  label: "AM Scorecard",     icon: BarChart3 },
      { to: "/kpiscorecard", label: "KPI Scorecard",    icon: ListChecks },
    ],
  },
  {
    heading: "Intelligence",
    items: [
      { to: "/matrix",       label: "Capability Matrix",   icon: Grid3X3 },
      { to: "/availability", label: "Availability Engine", icon: Users },
    ],
  },
];

export default function Sidebar() {
  const linkClasses = ({ isActive }: { isActive: boolean }) => clsx(
    "flex items-center gap-3 px-5 py-2.5 text-[13px] transition-colors border-l-2",
    isActive
      ? "border-l-satori-green bg-white/[0.04] text-white font-medium"
      : "border-l-transparent text-slate-400 hover:text-slate-100 hover:bg-white/[0.03]",
  );

  return (
    <aside className="w-64 shrink-0 bg-[#0b1220] text-slate-300 flex flex-col">
      <div className="px-5 py-5 flex items-center gap-2.5 border-b border-white/5">
        <img src="/static/tmc-logo-white.png" alt="TMC" className="h-7 w-auto object-contain shrink-0" />
        <div className="text-xl font-bold text-white tracking-tight">satori</div>
      </div>

      <nav className="flex-1 overflow-y-auto py-4">
        {GROUPS.map(group => (
          <div key={group.heading} className="mb-6">
            <div className="px-5 mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">
              {group.heading}
            </div>
            {group.items.map(({ to, label, icon: Icon }) => (
              <NavLink key={to} to={to} className={linkClasses}>
                <Icon className="w-4 h-4 shrink-0" />
                <span className="truncate">{label}</span>
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <div className="border-t border-white/5">
        <div className="px-5 pt-4 pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">System</div>
        <NavLink to="/settings" className={linkClasses}>
          <Cog className="w-4 h-4 shrink-0" />
          <span className="truncate">Settings</span>
        </NavLink>
        <div className="px-5 py-3 text-[9px] text-slate-600 uppercase tracking-[0.16em]">v2.0</div>
      </div>
    </aside>
  );
}
