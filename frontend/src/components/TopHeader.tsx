import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { Bell, HelpCircle, Moon, Sun } from "lucide-react";
import { useHealth } from "@/api/hooks";

const TITLES: Record<string, string> = {
  "/chat":         "Ask Me Anything",
  "/matrix":       "Capability Intelligence Matrix",
  "/availability": "Availability Engine",
  "/attendance":   "Attendance",
  "/timesheet":    "Timesheets",
  "/resources":    "Resource Allocation",
  "/employees":    "Employee Data",
  "/coverage":     "Account Coverage",
  "/pipeline":     "Pipeline Health",
  "/amscorecard":  "AM Scorecard",
  "/kpiscorecard": "KPI Scorecard",
  "/settings":     "Settings",
};

export default function TopHeader() {
  const { pathname } = useLocation();
  const title = TITLES[pathname] ?? "Satori";
  const { data: health } = useHealth();

  // Dark-mode toggle — persists across sessions, mirrors the legacy Satori UI.
  const [isDark, setIsDark] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    const stored = localStorage.getItem("satori-theme");
    if (stored) return stored === "dark";
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  });
  useEffect(() => {
    const root = document.documentElement;
    if (isDark) root.classList.add("dark"); else root.classList.remove("dark");
    localStorage.setItem("satori-theme", isDark ? "dark" : "light");
  }, [isDark]);

  return (
    <header className="h-16 bg-white dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between px-6 shrink-0">
      <div className="flex items-center gap-4">
        <h1 className="font-sans text-lg font-bold text-slate-900 dark:text-slate-100">{title}</h1>
        {health && (
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300 text-xs font-medium border border-green-200 dark:border-green-800">
            <span className="w-1.5 h-1.5 bg-green-500 rounded-full" />
            Enterprise AI &middot; Connected to your data sources
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={() => setIsDark(d => !d)}
          className="btn-ghost text-slate-600 dark:text-slate-300 dark:bg-slate-800 dark:border-slate-700 dark:hover:bg-slate-700"
          title={isDark ? "Switch to light mode" : "Switch to dark mode"}
        >
          {isDark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
          {isDark ? "Light" : "Dark"}
        </button>
        <button className="relative p-2 rounded-full hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-600 dark:text-slate-300">
          <Bell className="w-4 h-4" />
          <span className="absolute top-1 right-1 w-4 h-4 rounded-full bg-red-500 text-white text-[9px] flex items-center justify-center">3</span>
        </button>
        <button className="p-2 rounded-full hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-600 dark:text-slate-300">
          <HelpCircle className="w-4 h-4" />
        </button>
        <div className="flex items-center gap-2 pl-3 ml-1 border-l border-slate-200 dark:border-slate-700">
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-satori-green to-satori-teal flex items-center justify-center text-white text-xs font-bold">A</div>
          <span className="text-sm font-medium text-slate-800 dark:text-slate-200">Adeel</span>
        </div>
      </div>
    </header>
  );
}
