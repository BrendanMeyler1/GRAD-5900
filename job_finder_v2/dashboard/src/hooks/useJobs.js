import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";

const JOBS_KEY = ["jobs"];

function buildJobsUrl(filters) {
  const params = new URLSearchParams();
  if (filters) {
    if (filters.status) params.set("status", filters.status);
    if (filters.source) params.set("source", filters.source);
    if (filters.min_fit_score != null) params.set("min_fit_score", String(filters.min_fit_score));
    if (filters.remote_only) params.set("remote_only", "true");
    if (filters.title_query) params.set("title_query", filters.title_query);
    if (filters.sort_by) params.set("sort_by", filters.sort_by);
  }
  const qs = params.toString();
  return qs ? `/api/jobs?${qs}` : "/api/jobs";
}

export function useJobs(filters) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [...JOBS_KEY, filters],
    queryFn: () => api.get(buildJobsUrl(filters)),
  });

  return { jobs: data, isLoading, error, refetch };
}

export function useSearchJobs() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (searchParams) => api.post("/api/jobs/search", searchParams),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: JOBS_KEY });
    },
  });
}

export function useJobDetail(jobId) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [...JOBS_KEY, jobId],
    queryFn: () => api.get(`/api/jobs/${jobId}`),
    enabled: Boolean(jobId),
  });

  return { job: data, isLoading, error, refetch };
}

export function useQueueJob() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (jobId) => api.post(`/api/jobs/${jobId}/queue`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: JOBS_KEY });
    },
  });
}

export function useSkipJob() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (jobId) => api.post(`/api/jobs/${jobId}/skip`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: JOBS_KEY });
    },
  });
}
