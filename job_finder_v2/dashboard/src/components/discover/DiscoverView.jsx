import { useState, useCallback } from "react";
import SearchBar from "./SearchBar";
import JobCard from "./JobCard";
import JobDetail from "./JobDetail";
import SkeletonCard from "../shared/SkeletonCard";
import EmptyState from "../shared/EmptyState";
import ErrorState from "../shared/ErrorState";
import { useJobs, useSearchJobs } from "../../hooks/useJobs";
import { useTaskStatus } from "../../hooks/useTaskStatus";
import { useQueryClient } from "@tanstack/react-query";
import { Search } from "lucide-react";

export default function DiscoverView() {
  const [selectedJob, setSelectedJob] = useState(null);
  const [searchTaskId, setSearchTaskId] = useState(null);
  const [activeFilters, setActiveFilters] = useState({
    sort_by: "fit_score",
    status: null,
    source: null,
    min_fit_score: null,
    remote_only: false,
  });

  const queryClient = useQueryClient();
  const { jobs, isLoading: jobsLoading, error: jobsError, refetch } = useJobs(activeFilters);
  const searchMutation = useSearchJobs();

  // Poll task status while search is running
  const { task: searchTask, isPolling } = useTaskStatus(searchTaskId);

  // When the task completes, refresh jobs
  const prevTaskStatus = searchTask?.status;
  if (prevTaskStatus === "completed" && searchTaskId) {
    queryClient.invalidateQueries({ queryKey: ["jobs"] });
  }

  const handleSearch = useCallback(
    async (query, location, filters) => {
      setSearchTaskId(null);
      try {
        const result = await searchMutation.mutateAsync({
          query,
          location: location || "",
          limit: 30,
        });
        if (result?.task_id) {
          setSearchTaskId(result.task_id);
        }
      } catch (err) {
        console.error("Search failed:", err);
      }
      if (filters) {
        setActiveFilters((prev) => ({ ...prev, ...filters }));
      }
    },
    [searchMutation]
  );

  const isSearching = searchMutation.isPending || isPolling;

  const jobList = jobs || [];

  return (
    <div className="flex h-full flex-col">
      {/* Search bar */}
      <div className="border-b border-slate-700/50 bg-slate-900 px-6 py-4">
        <SearchBar onSearch={handleSearch} isSearching={isSearching} />
      </div>

      {/* Main content: list + detail pane */}
      <div className="flex min-h-0 flex-1">
        {/* Job list */}
        <div className="w-96 shrink-0 overflow-y-auto border-r border-slate-700/50 bg-slate-900">
          {jobsError ? (
            <div className="p-4">
              <ErrorState
                message="Failed to load jobs. Check your API connection."
                onRetry={refetch}
              />
            </div>
          ) : isSearching && jobList.length === 0 ? (
            <div className="space-y-2 p-4">
              {[1, 2, 3].map((i) => (
                <SkeletonCard key={i} />
              ))}
            </div>
          ) : jobList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={Search}
                title="No jobs yet"
                description="Search above to discover opportunities, or paste a URL in Chat to add a specific posting."
              />
            </div>
          ) : (
            <div className="space-y-1 p-3">
              {isSearching && (
                <div className="mb-2 flex items-center gap-2 rounded-lg bg-indigo-500/10 px-3 py-2 text-sm text-indigo-400">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-indigo-400" />
                  Searching for more jobs…
                </div>
              )}
              {jobList.map((job) => (
                <JobCard
                  key={job.id}
                  job={job}
                  selected={selectedJob?.id === job.id}
                  onClick={setSelectedJob}
                />
              ))}
            </div>
          )}
        </div>

        {/* Detail pane */}
        <div className="min-w-0 flex-1 overflow-y-auto bg-slate-950">
          {selectedJob ? (
            <JobDetail job={selectedJob} onJobUpdated={() => refetch()} />
          ) : (
            <div className="flex h-full items-center justify-center">
              <p className="text-slate-500">Select a job to see details</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
