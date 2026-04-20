import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";

const APPLICATIONS_KEY = ["applications"];

// Statuses that indicate a background task is still running.
// While any application has one of these statuses the hook polls automatically.
const IN_PROGRESS_STATUSES = new Set(["shadow_running", "submitting", "pending"]);

export function useApplications(status) {
  const url = status ? `/api/applications?status=${encodeURIComponent(status)}` : "/api/applications";

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [...APPLICATIONS_KEY, { status }],
    queryFn: () => api.get(url),
    // Poll every 3 s while any application is still in-flight; stop when idle.
    refetchInterval: (query) => {
      const apps = query.state.data ?? [];
      return apps.some((a) => IN_PROGRESS_STATUSES.has(a.status)) ? 3000 : false;
    },
  });

  return { applications: data, isLoading, error, refetch };
}

export function usePendingApplications() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [...APPLICATIONS_KEY, "pending"],
    queryFn: () => api.get("/api/applications/pending"),
  });

  return { applications: data, isLoading, error, refetch };
}

export function useApplicationDetail(appId) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [...APPLICATIONS_KEY, appId],
    queryFn: () => api.get(`/api/applications/${appId}`),
    enabled: Boolean(appId),
  });

  return { application: data, isLoading, error, refetch };
}

export function useUpdateApplication() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ appId, data }) => api.patch(`/api/applications/${appId}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: APPLICATIONS_KEY });
    },
  });
}

export function useShadowApply() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (jobId) => api.post(`/api/apply/${jobId}/shadow`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: APPLICATIONS_KEY });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useApproveApplication() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (appId) => api.post(`/api/apply/${appId}/approve`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: APPLICATIONS_KEY });
    },
  });
}

/**
 * Delete a single application record (the per-card × button).
 */
export function useDeleteApplication() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (appId) => api.delete(`/api/applications/${appId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: APPLICATIONS_KEY });
    },
  });
}

/**
 * Bulk-delete all applications whose status matches any of the provided statuses.
 * Used by the per-column "Clear all" button.
 * @param {string[]} statuses - e.g. ["shadow_review", "awaiting_approval"]
 */
export function useClearApplications() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (statuses) => {
      const params = new URLSearchParams();
      statuses.forEach((s) => params.append("status", s));
      return api.delete(`/api/applications?${params.toString()}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: APPLICATIONS_KEY });
    },
  });
}
