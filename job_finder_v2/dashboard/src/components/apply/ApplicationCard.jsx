import { Clock, Loader2 } from "lucide-react";
import StatusBadge from "../shared/StatusBadge";
import EmailPill from "../shared/EmailPill";

function relativeTime(dateString) {
  if (!dateString) return "";
  const now = Date.now();
  const then = new Date(dateString).getTime();
  const diffSec = Math.floor((now - then) / 1000);

  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  return new Date(dateString).toLocaleDateString();
}

const IN_PROGRESS_STATUSES = new Set(["shadow_running", "submitting"]);

export default function ApplicationCard({ application, onClick }) {
  const {
    status,
    updated_at,
    created_at,
    email_event,
    job,
  } = application || {};

  const company = job?.company;
  const job_title = job?.title;

  const isInProgress = IN_PROGRESS_STATUSES.has(status);

  return (
    <button
      type="button"
      onClick={() => onClick?.(application)}
      className="group w-full rounded-lg bg-slate-800 p-3 text-left transition-colors hover:bg-slate-750 hover:ring-1 hover:ring-slate-600 focus:outline-none focus:ring-2 focus:ring-indigo-500"
    >
      {/* Company + timestamp row */}
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-bold text-slate-100 truncate">
          {company || "Unknown Company"}
        </span>
        <span className="flex shrink-0 items-center gap-1 text-xs text-slate-500">
          <Clock className="h-3 w-3" />
          {relativeTime(updated_at || created_at)}
        </span>
      </div>

      {/* Job title */}
      <p className="mt-0.5 text-xs text-slate-400 truncate">
        {job_title || "Untitled Position"}
      </p>

      {/* Badge row */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <StatusBadge status={status} />
        {email_event?.category && (
          <EmailPill
            category={email_event.category}
            summary={email_event.summary}
          />
        )}
      </div>

      {/* In-progress animated stripe bar */}
      {isInProgress && (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-700">
          <div className="h-full w-full animate-indigo-stripe rounded-full bg-gradient-to-r from-indigo-500 via-indigo-400 to-indigo-500 bg-[length:200%_100%]" />
        </div>
      )}
    </button>
  );
}
