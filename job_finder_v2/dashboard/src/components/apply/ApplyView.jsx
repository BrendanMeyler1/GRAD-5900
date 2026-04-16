import { useState } from "react";
import PipelineColumn from "./PipelineColumn";
import ApplicationCard from "./ApplicationCard";
import ReviewPanel from "./ReviewPanel";
import { useApplications } from "../../hooks/useApplications";
import LoadingSpinner from "../shared/LoadingSpinner";
import ErrorState from "../shared/ErrorState";

// Status → column mapping
const COLUMNS = [
  {
    key: "in_progress",
    title: "In Progress",
    statuses: ["shadow_running", "submitting", "pending"],
  },
  {
    key: "review",
    title: "Review",
    statuses: ["shadow_review", "awaiting_approval"],
  },
  {
    key: "submitted",
    title: "Submitted",
    statuses: ["submitted"],
  },
  {
    key: "skipped",
    title: "Skipped / Failed",
    statuses: ["skipped", "failed", "rejected", "aborted"],
  },
];

export default function ApplyView() {
  const [reviewApp, setReviewApp] = useState(null);

  const {
    applications = [],
    isLoading,
    error,
    refetch,
  } = useApplications();

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
        <ErrorState
          message="Failed to load applications."
          onRetry={refetch}
        />
      </div>
    );
  }

  // Group by column
  const grouped = COLUMNS.reduce((acc, col) => {
    acc[col.key] = applications.filter((a) =>
      col.statuses.includes(a.status)
    );
    return acc;
  }, {});

  return (
    <div className="relative flex h-full flex-col">
      <div className="flex min-h-0 flex-1 gap-4 overflow-x-auto p-6">
        {COLUMNS.map((col) => {
          const apps = grouped[col.key] || [];
          return (
            <PipelineColumn key={col.key} title={col.title} count={apps.length}>
              {apps.length === 0 ? (
                <p className="py-6 text-center text-xs text-slate-500">
                  No applications here yet
                </p>
              ) : (
                apps.map((app) => (
                  <ApplicationCard
                    key={app.id}
                    application={app}
                    onClick={
                      col.key === "review"
                        ? () => setReviewApp(app)
                        : undefined
                    }
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
