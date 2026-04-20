import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api/client";

const PROFILE_KEY = ["profile"];

export function useProfile() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: PROFILE_KEY,
    queryFn: () => api.get("/api/profile"),
  });

  return { profile: data, isLoading, error, refetch };
}

export function useUploadResume() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (file) => api.upload("/api/profile/resume", file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: PROFILE_KEY });
    },
  });
}

export function useUpdateProfile() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (profileData) => api.put("/api/profile", profileData),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: PROFILE_KEY });
    },
  });
}
