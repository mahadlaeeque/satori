import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, RotateCw, ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "@/api/client";
import clsx from "clsx";

interface AttendanceLike {
  rows: Record<string, any>[];
  columns: string[];
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
  table_name: string;
  error?: string;
}

interface DataTableProps {
  table: string;
  title?: string;
  initialPerPage?: number;
  /** Per-column display formatter (optional) */
  format?: (col: string, val: any) => React.ReactNode;
  /** Override which columns to show; defaults to all */
  columns?: string[];
}

const DOLLAR_COLS = new Set([
  "col_2026_target", "q1_ach", "open_pipeline", "q1_target",
  "remaining_2026", "crm_pipeline", "historical_won", "historical_lost",
]);
const RATE_COLS = new Set([
  "hist_win_rate", "win_rate_by", "q1_of_plan", "utilisation", "weight",
]);

function formatColName(col: string) {
  return col.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function defaultFormat(col: string, val: any): React.ReactNode {
  if (val === null || val === undefined) return <span className="text-slate-500 dark:text-slate-500">—</span>;
  const lower = col.toLowerCase();
  if (DOLLAR_COLS.has(lower) && typeof val === "number") {
    return "$" + val.toLocaleString("en-US", { maximumFractionDigits: 0 });
  }
  if (RATE_COLS.has(lower) && typeof val === "number") {
    return (val * 100).toFixed(1) + "%";
  }
  if (typeof val === "boolean") return val ? "✓" : "—";
  if (typeof val === "number") return val.toLocaleString();
  const s = String(val);
  return s.length > 200 ? s.slice(0, 200) + "…" : s;
}

export default function DataTable({ table, title, initialPerPage = 25, format = defaultFormat, columns }: DataTableProps) {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [searchDraft, setSearchDraft] = useState("");

  const { data, isLoading, error, refetch, isFetching } = useQuery<AttendanceLike>({
    queryKey: ["table", table, page, search, initialPerPage],
    queryFn: () => {
      const qs = new URLSearchParams({
        table,
        page: String(page),
        per_page: String(initialPerPage),
        search,
      });
      return api.get<AttendanceLike>(`/api/attendance?${qs.toString()}`);
    },
  });

  function submitSearch() {
    setPage(1);
    setSearch(searchDraft.trim());
  }

  const visibleCols = columns ?? data?.columns ?? [];

  return (
    <div className="h-full flex flex-col">
      {/* Toolbar */}
      <div className="px-6 py-3 border-b border-slate-200/80 dark:border-slate-800 flex items-center gap-3 shrink-0 bg-slate-50 dark:bg-satori-ink">
        <div className="flex-1 relative max-w-md">
          <Search className="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500 dark:text-slate-400" />
          <input
            className="input pl-8 w-full"
            placeholder="Search…"
            value={searchDraft}
            onChange={e => setSearchDraft(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") submitSearch(); }}
          />
        </div>
        <button onClick={submitSearch} className="btn-ghost">
          Search
        </button>
        <button onClick={() => refetch()} className="btn-ghost" disabled={isFetching}>
          <RotateCw className={clsx("w-3.5 h-3.5", isFetching && "animate-spin")} />
        </button>
        <div className="ml-auto text-xs text-slate-500 dark:text-slate-400">
          {data?.total?.toLocaleString() ?? "—"} records
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto bg-white dark:bg-satori-ink">
        {error ? (
          <div className="m-6 card p-4 text-sm text-red-500 dark:text-red-400">
            Failed to load: {(error as Error).message}
          </div>
        ) : isLoading ? (
          <div className="text-center text-slate-500 dark:text-slate-400 py-12 text-sm">Loading {title ?? table}…</div>
        ) : !data?.rows?.length ? (
          <div className="text-center text-slate-500 dark:text-slate-400 py-12 text-sm">No records.</div>
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 z-10">
              <tr className="bg-slate-100 dark:bg-satori-paper text-slate-700 dark:text-slate-200">
                {visibleCols.map(c => (
                  <th key={c} className="px-3 py-2.5 text-left font-semibold whitespace-nowrap border-b border-slate-200 dark:border-slate-700">
                    {formatColName(c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, i) => (
                <tr key={i} className="hover:bg-slate-100/30 dark:hover:bg-slate-800/40 border-b border-slate-200/40 dark:border-slate-800/60">
                  {visibleCols.map(c => (
                    <td key={c} className="px-3 py-2 text-slate-700 dark:text-slate-200 align-top">
                      {format(c, row[c])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {data && data.total_pages > 1 && (
        <div className="px-6 py-2 border-t border-slate-200/80 dark:border-slate-800 flex items-center justify-between bg-slate-50 dark:bg-satori-ink shrink-0">
          <div className="text-xs text-slate-500 dark:text-slate-400">
            Page {data.page} of {data.total_pages}
          </div>
          <div className="flex gap-1">
            <button
              className="btn-ghost text-xs"
              disabled={page <= 1}
              onClick={() => setPage(p => Math.max(1, p - 1))}
            >
              <ChevronLeft className="w-3.5 h-3.5" /> Prev
            </button>
            <button
              className="btn-ghost text-xs"
              disabled={page >= data.total_pages}
              onClick={() => setPage(p => p + 1)}
            >
              Next <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
