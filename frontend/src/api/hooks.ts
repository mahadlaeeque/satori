/**
 * TanStack Query hooks for Satori's FastAPI endpoints.
 * Pattern: one hook per logical query. Mutations expose mutateAsync().
 */

import {
  useQuery,
  useMutation,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";
import { api } from "./client";
import type {
  AskResponse,
  AttendanceAnalyticsResponse,
  AvailabilityResponse,
  CapabilityMatrixResponse,
  Conversation,
  ConversationSummary,
  Department,
  EmployeePattern,
  PredictiveResponse,
  ProductivityTip,
  StaffSuggestion,
  StaffSuggestionsResponse,
} from "./types";

// ── Chat / history ────────────────────────────────────────────────────
export const useConversations = (opts?: UseQueryOptions<ConversationSummary[]>) =>
  useQuery<ConversationSummary[]>({
    queryKey: ["history"],
    queryFn: () => api.get<ConversationSummary[]>("/history"),
    ...opts,
  });

export const useConversation = (id: string | null) =>
  useQuery<Conversation>({
    queryKey: ["history", id],
    queryFn: () => api.get<Conversation>(`/history/${id}`),
    enabled: !!id,
  });

export const useAskSatori = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { question: string; conversation_id?: string; lang?: string }) =>
      api.post<AskResponse>("/ask", vars),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["history"] }),
  });
};

export const useDeleteConversation = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del<{ status: string }>(`/history/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["history"] }),
  });
};

export const useRenameConversation = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: string; title: string }) =>
      api.post<{ status: string }>(`/history/${vars.id}/rename`, { title: vars.title }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["history"] }),
  });
};

// ── Availability ──────────────────────────────────────────────────────
export const useAvailability = (params: { search?: string; status?: string; competency?: string }) =>
  useQuery<AvailabilityResponse>({
    queryKey: ["availability", params],
    queryFn: () => {
      const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => !!v) as any).toString();
      return api.get<AvailabilityResponse>(`/api/availability${qs ? "?" + qs : ""}`);
    },
  });

export const useDepartments = () =>
  useQuery<{ departments: Department[] }>({
    queryKey: ["departments"],
    queryFn: () => api.get("/api/departments"),
  });

export const useStaffSuggestions = () =>
  useMutation({
    mutationFn: (vars: { project_name: string; project_description: string; department: string; skills_required: string }) =>
      api.post<StaffSuggestionsResponse>("/api/ai-staff-suggestions", vars),
  });

// ── Predictive ────────────────────────────────────────────────────────
export const usePredictive = (weeks: number = 8) =>
  useQuery<PredictiveResponse>({
    queryKey: ["predictive", weeks],
    queryFn: () => api.get<PredictiveResponse>(`/api/predictive-attendance?weeks=${weeks}`),
  });

export const useEmployeePattern = (name: string | null, weeks: number = 16) =>
  useQuery<EmployeePattern>({
    queryKey: ["employee-pattern", name, weeks],
    queryFn: () => api.get<EmployeePattern>(
      `/api/employee-pattern?name=${encodeURIComponent(name!)}&weeks=${weeks}`,
    ),
    enabled: !!name,
  });

export const useProductivityTip = () =>
  useMutation({
    mutationFn: (vars: { name: string; pattern: EmployeePattern }) =>
      api.post<ProductivityTip>("/api/ai-productivity-tip", vars),
  });

// ── Capability matrix ────────────────────────────────────────────────
export const useCapabilityMatrix = () =>
  useQuery<CapabilityMatrixResponse>({
    queryKey: ["capability-matrix"],
    queryFn: () => api.get<CapabilityMatrixResponse>("/api/capability-matrix"),
  });

// ── Attendance analytics ─────────────────────────────────────────────
export const useAttendanceAnalytics = (params: {
  range?: number;
  department?: string;
  employee?: string;
  date_from?: string;
  date_to?: string;
}) =>
  useQuery<AttendanceAnalyticsResponse>({
    queryKey: ["attendance-analytics", params],
    queryFn: () => {
      const qs = new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== "" && v !== null)
          .map(([k, v]) => [k, String(v)]),
      ).toString();
      return api.get<AttendanceAnalyticsResponse>(
        `/api/attendance-analytics${qs ? "?" + qs : ""}`,
      );
    },
  });

// ── Health (sanity / status) ──────────────────────────────────────────
export const useHealth = () =>
  useQuery<{ status: string; project: string; table: string }>({
    queryKey: ["health"],
    queryFn: () => api.get("/api/health"),
    staleTime: 60_000,
  });

// Export the raw suggestion type so panels can re-import it from here
export type { StaffSuggestion };
