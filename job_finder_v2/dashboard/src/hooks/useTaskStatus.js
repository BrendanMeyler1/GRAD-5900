import { useQuery } from "@tanstack/react-query";
import api from "../api/client";

export function useTaskStatus(taskId) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["tasks", taskId],
    queryFn: () => api.get(`/api/tasks/${taskId}`),
    enabled: Boolean(taskId),
    refetchInterval: (query) => {
      const task = query.state.data;
      if (task && (task.status === "completed" || task.status === "failed")) {
        return false;
      }
      return 3000;
    },
  });

  const isPolling =
    Boolean(taskId) && Boolean(data) && data.status === "running";

  return { task: data, isPolling, isLoading, error };
}
