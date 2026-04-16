import clsx from "clsx";
import { MapPin, Wifi } from "lucide-react";
import FitBadge from "../shared/FitBadge";

const SOURCE_LABELS = {
  greenhouse: "GH",
  linkedin: "LI",
  lever: "LV",
};

function relativeTime(dateStr) {
  if (!dateStr) return "";
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffMs = now - then;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  const diffMonth = Math.floor(diffDay / 30);
  return `${diffMonth}mo ago`;
}

export default function JobCard({ job, selected = false, onClick }) {
  const isApplied = job.status === "applied" || job.status === "submitted";
  const sourceLabel = SOURCE_LABELS[job.source] || job.source?.toUpperCase()?.slice(0, 2) || "??";

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onClick?.(job)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick?.(job);
        }
      }}
      className={clsx(
        "relative cursor-pointer rounded-lg p-4 transition-colors",
        selected
          ? "border-l-4 border-indigo-500 bg-slate-800/80"
          : "border-l-4 border-transparent bg-slate-800 hover:bg-slate-800/70",
        isApplied && "opacity-60"
      )}
    >
      {/* Applied overlay */}
      {isApplied && (
        <span className="absolute right-3 top-3 rounded-full bg-emerald-500/20 px-2 py-0.5 text-xs font-medium text-emerald-400">
          Applied &#10003;
        </span>
      )}

      <div className="flex items-start justify-between gap-3">
        {/* Left content */}
        <div className="min-w-0 flex-1">
          <p className="text-sm text-slate-400">{job.company || "Unknown Company"}</p>
          <h3 className="mt-0.5 truncate text-lg font-semibold text-slate-100">
            {job.title || "Untitled Position"}
          </h3>

          <div className="mt-1.5 flex items-center gap-2 text-xs text-slate-400">
            {job.location && (
              <span className="flex items-center gap-1">
                <MapPin className="h-3 w-3" />
                {job.location}
              </span>
            )}
            {job.remote_ok && (
              <span className="flex items-center gap-1 rounded-full bg-indigo-500/20 px-2 py-0.5 text-indigo-400">
                <Wifi className="h-3 w-3" />
                Remote
              </span>
            )}
          </div>
        </div>

        {/* Right side: FitBadge */}
        <FitBadge score={job.fit_score} />
      </div>

      {/* Bottom row: source chip + posted time */}
      <div className="mt-3 flex items-center gap-2">
        <span className="rounded bg-slate-700 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
          {sourceLabel}
        </span>
        {job.posted_at && (
          <span className="text-xs text-slate-500">{relativeTime(job.posted_at)}</span>
        )}
      </div>
    </div>
  );
}
