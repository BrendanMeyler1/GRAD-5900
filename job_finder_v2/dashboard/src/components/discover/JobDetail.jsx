import { useState } from "react";
import {
  MapPin,
  Wifi,
  CheckCircle,
  XCircle,
  Loader2,
  ExternalLink,
  ListPlus,
  SkipForward,
} from "lucide-react";
import clsx from "clsx";
import FitBadge from "../shared/FitBadge";
import { useShadowApply } from "../../hooks/useApplications";
import { useQueueJob, useSkipJob } from "../../hooks/useJobs";
import { useToast } from "../../hooks/useToast";

function stripHtml(html) {
  if (!html) return "";
  const doc = new DOMParser().parseFromString(html, "text/html");
  return doc.body.textContent || "";
}

function likelihoodLabel(score) {
  if (score == null) return null;
  if (score >= 75) return { text: "High", color: "text-emerald-400" };
  if (score >= 45) return { text: "Medium", color: "text-amber-400" };
  return { text: "Low", color: "text-rose-400" };
}

export default function JobDetail({ job }) {
  const shadowApply = useShadowApply();
  const queueJob = useQueueJob();
  const skipJob = useSkipJob();
  const { addToast } = useToast();
  const [appliedId, setAppliedId] = useState(null);

  if (!job) return null;

  const score = job.fit_score;
  const likelihood = likelihoodLabel(job.interview_likelihood ?? score);
  const description = stripHtml(job.description || job.summary || "");

  const handleShadowApply = async () => {
    try {
      const result = await shadowApply.mutateAsync(job.id);
      setAppliedId(result?.application_id || job.id);
      addToast({
        message: "Shadow application created! Review it in the Apply tab.",
        type: "success",
      });
    } catch (err) {
      addToast({
        message: err?.message || "Shadow apply failed. Please try again.",
        type: "error",
      });
    }
  };

  const handleQueue = async () => {
    try {
      await queueJob.mutateAsync(job.id);
      addToast({ message: "Job queued for later.", type: "info" });
    } catch (err) {
      addToast({
        message: err?.message || "Failed to queue job.",
        type: "error",
      });
    }
  };

  const handleSkip = async () => {
    try {
      await skipJob.mutateAsync(job.id);
      addToast({ message: "Job skipped.", type: "info" });
    } catch (err) {
      addToast({
        message: err?.message || "Failed to skip job.",
        type: "error",
      });
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl bg-slate-800">
      {/* Header */}
      <div className="border-b border-slate-700/50 p-5">
        <h2 className="text-xl font-bold text-slate-100">
          {job.title || "Untitled Position"}
        </h2>
        <p className="mt-1 text-sm text-slate-400">
          {job.company || "Unknown Company"}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
          {job.location && (
            <span className="flex items-center gap-1">
              <MapPin className="h-3.5 w-3.5" />
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

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto p-5">
        {/* Fit section */}
        <div className="mb-6 rounded-lg bg-slate-900 p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium text-slate-300">Fit Score</span>
              <FitBadge score={score} />
            </div>
            {likelihood && (
              <span className={clsx("text-xs font-medium", likelihood.color)}>
                {likelihood.text} interview likelihood
              </span>
            )}
          </div>

          {/* Progress bar */}
          {score != null && (
            <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-700">
              <div
                className={clsx(
                  "h-full rounded-full transition-all duration-500",
                  score >= 70
                    ? "bg-emerald-500"
                    : score >= 40
                      ? "bg-amber-500"
                      : "bg-rose-500"
                )}
                style={{ width: `${Math.min(score, 100)}%` }}
              />
            </div>
          )}
        </div>

        {/* Strengths */}
        {job.strengths?.length > 0 && (
          <div className="mb-5">
            <h4 className="mb-2 text-sm font-medium text-slate-300">Strengths</h4>
            <ul className="space-y-1.5">
              {job.strengths.map((s, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-slate-300">
                  <CheckCircle className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
                  {s}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Gaps */}
        {job.gaps?.length > 0 && (
          <div className="mb-5">
            <h4 className="mb-2 text-sm font-medium text-slate-300">Gaps</h4>
            <ul className="space-y-1.5">
              {job.gaps.map((g, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-slate-300">
                  <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
                  {g}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Description */}
        {description && (
          <div className="mb-5">
            <h4 className="mb-2 text-sm font-medium text-slate-300">Description</h4>
            <div className="max-h-64 overflow-y-auto rounded-lg bg-slate-900 p-3 text-sm leading-relaxed text-slate-400">
              {description}
            </div>
          </div>
        )}
      </div>

      {/* Action buttons */}
      <div className="border-t border-slate-700/50 p-4">
        <div className="flex items-center gap-3">
          <button
            onClick={handleShadowApply}
            disabled={shadowApply.isPending}
            className={clsx(
              "flex flex-1 items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold text-white transition-colors",
              shadowApply.isPending
                ? "cursor-not-allowed bg-indigo-500/50"
                : "bg-indigo-500 hover:bg-indigo-600 active:bg-indigo-700"
            )}
          >
            {shadowApply.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Applying...
              </>
            ) : (
              "Shadow Apply"
            )}
          </button>

          <button
            onClick={handleQueue}
            disabled={queueJob.isPending}
            className="flex items-center gap-1.5 rounded-lg border border-slate-600 px-4 py-2.5 text-sm font-medium text-slate-300 transition-colors hover:border-slate-500 hover:text-slate-100"
          >
            <ListPlus className="h-4 w-4" />
            Queue
          </button>

          <button
            onClick={handleSkip}
            disabled={skipJob.isPending}
            className="flex items-center gap-1.5 rounded-lg px-4 py-2.5 text-sm font-medium text-slate-400 transition-colors hover:text-slate-200"
          >
            <SkipForward className="h-4 w-4" />
            Skip
          </button>
        </div>

        {/* Post-apply link to Apply tab */}
        {appliedId && (
          <a
            href="/apply"
            className="mt-3 flex items-center justify-center gap-1.5 text-xs text-indigo-400 hover:text-indigo-300"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            View in Apply tab
          </a>
        )}
      </div>
    </div>
  );
}
