import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";

const APPLICATIONS_KEY = ["applications"];

export function useApplications(status) {
  const url = status ? `/api/applications?status=${encodeURIComponent(status)}` : "/api/applications";

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [...APPLICATIONS_KEY, { status }],
    queryFn: () => api.get(url),
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
