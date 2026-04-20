import { useState } from "react";
import PipelineColumn from "./PipelineColumn";
import ApplicationCard from "./ApplicationCard";
import ReviewPanel from "./ReviewPanel";
import { useApplications, useDeleteApplication, useClearApplications } from "../../hooks/useApplications";
import { useToast } from "../../hooks/useToast";
import LoadingSpinner from "../shared/LoadingSpinner";
import ErrorState from "../shared/ErrorState";

// Status → column mapping.
// clearable: whether the column shows a "Clear all" button.
// In Progress is excluded — deleting an actively-running task would orphan backend work.
const COLUMNS = [
  {
    key: "in_progress",
    title: "In Progress",
    statuses: ["shadow_running", "submitting", "pending"],
    clearable: false,
  },
  {
    key: "review",
    title: "Review",
    statuses: ["shadow_review", "awaiting_approval"],
    clearable: true,
  },
  {
    key: "submitted",
    title: "Submitted",
    statuses: ["submitted"],
    clearable: true,
  },
  {
    key: "skipped",
    title: "Skipped / Failed",
    statuses: ["skipped", "failed", "rejected", "aborted"],
    clearable: true,
  },
];

export default function ApplyView() {
  const [reviewApp, setReviewApp] = useState(null);

  const { applications = [], isLoading, error, refetch } = useApplications();
  const deleteMutation = useDeleteApplication();
  const clearMutation = useClearApplications();
  const { addToast } = useToast();

  const handleDelete = (appId) => {
    deleteMutation.mutate(appId, {
      onSuccess: () => addToast({ message: "Application removed.", type: "success" }),
      onError: () => addToast({ message: "Failed to remove application.", type: "error" }),
    });
    // If the deleted card was open in the ReviewPanel, close it
    if (reviewApp?.id === appId) setReviewApp(null);
  };

  const handleClear = (statuses) => {
    clearMutation.mutate(statuses, {
      onSuccess: (data) =>
        addToast({
          message: `Cleared ${data?.deleted ?? "all"} application${data?.deleted !== 1 ? "s" : ""}.`,
          type: "success",
        }),
      onError: () => addToast({ message: "Failed to clear applications.", type: "error" }),
    });
    // Close the panel if the open app is being cleared
    if (reviewApp && statuses.includes(reviewApp.status)) setReviewApp(null);
  };

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <LoadingSpinner label="Loading applications…" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <ErrorState message="Failed to load applications." onRetry={refetch} />
      </div>
    );
  }

  // Group applications into their kanban column
  const grouped = COLUMNS.reduce((acc, col) => {
    acc[col.key] = applications.filter((a) => col.statuses.includes(a.status));
    return acc;
  }, {});

  return (
    <div className="relative flex h-full flex-col">
      <div className="flex min-h-0 flex-1 gap-4 overflow-x-auto p-6">
        {COLUMNS.map((col) => {
          const apps = grouped[col.key] || [];
          return (
            <PipelineColumn
              key={col.key}
              title={col.title}
              count={apps.length}
              onClear={col.clearable ? () => handleClear(col.statuses) : undefined}
            >
              {apps.length === 0 ? (
                <p className="py-6 text-center text-xs text-slate-500">
                  No applications here yet
                </p>
              ) : (
                apps.map((app) => (
                  <ApplicationCard
                    key={app.id}
                    application={app}
                    onClick={col.key === "review" ? () => setReviewApp(app) : undefined}
                    onDelete={col.clearable ? () => handleDelete(app.id) : undefined}
                  />
                ))
              )}
            </PipelineColumn>
          );
        })}
      </div>

      {/* Review panel slide-up */}
      {reviewApp && (
        <ReviewPanel
          application={reviewApp}
          onClose={() => setReviewApp(null)}
        />
      )}
    </div>
  );
}
