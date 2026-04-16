import { Link } from "react-router-dom";
import {
  ChevronRight,
  ChevronLeft,
  MapPin,
  Building2,
  CheckCircle2,
  XCircle,
  Image,
  FileText,
  User,
  Search,
} from "lucide-react";
import clsx from "clsx";
import FitBadge from "../shared/FitBadge";
import StatusBadge from "../shared/StatusBadge";

/* ------------------------------------------------------------------ */
/*  Completion ring (mirrors ProfileCompletionBar style)               */
/* ------------------------------------------------------------------ */
function CompletionRing({ percent }) {
  const radius = 28;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (percent / 100) * circumference;

  function strokeColor() {
    if (percent >= 60) return "#6366f1";
    if (percent >= 30) return "#f59e0b";
    return "#f43f5e";
  }

  function textColor() {
    if (percent >= 60) return "text-indigo-500";
    if (percent >= 30) return "text-amber-500";
    return "text-rose-500";
  }

  return (
    <svg className="mx-auto h-20 w-20" viewBox="0 0 64 64">
      <circle
        cx="32"
        cy="32"
        r={radius}
        fill="none"
        stroke="#334155"
        strokeWidth="4"
      />
      <circle
        cx="32"
        cy="32"
        r={radius}
        fill="none"
        stroke={strokeColor()}
        strokeWidth="4"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={offset}
        transform="rotate(-90 32 32)"
        className="transition-all duration-500"
      />
      <text
        x="32"
        y="32"
        textAnchor="middle"
        dominantBaseline="central"
        className={clsx("text-xs font-bold", textColor())}
        fill="currentColor"
      >
        {percent}%
      </text>
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/*  Section panels                                                     */
/* ------------------------------------------------------------------ */

function JobPanel({ data }) {
  if (!data) return null;

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-slate-200">
        {data.title || "Untitled Role"}
      </h3>

      {data.company && (
        <div className="flex items-center gap-1.5 text-xs text-slate-400">
          <Building2 className="h-3.5 w-3.5" />
          <span>{data.company}</span>
        </div>
      )}

      {data.location && (
        <div className="flex items-center gap-1.5 text-xs text-slate-400">
          <MapPin className="h-3.5 w-3.5" />
          <span>{data.location}</span>
        </div>
      )}

      {data.fit_score !== undefined && data.fit_score !== null && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">Fit</span>
          <FitBadge score={data.fit_score} />
        </div>
      )}

      {data.strengths?.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-slate-400">Strengths</p>
          <ul className="space-y-1">
            {data.strengths.map((s, i) => (
              <li
                key={i}
                className="flex items-start gap-1.5 text-xs text-slate-300"
              >
                <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500" />
                {s}
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.gaps?.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-slate-400">Gaps</p>
          <ul className="space-y-1">
            {data.gaps.map((g, i) => (
              <li
                key={i}
                className="flex items-start gap-1.5 text-xs text-slate-300"
              >
                <XCircle className="mt-0.5 h-3 w-3 shrink-0 text-rose-500" />
                {g}
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.id && (
        <Link
          to={`/discover?job=${data.id}`}
          className="mt-2 inline-block text-xs font-medium text-indigo-400 hover:text-indigo-300"
        >
          View in Discover &rarr;
        </Link>
      )}
    </div>
  );
}

function ApplyPanel({ data }) {
  if (!data) return null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-200">Application</h3>
        {data.status && <StatusBadge status={data.status} />}
      </div>

      {data.screenshot_count != null && (
        <div className="flex items-center gap-1.5 text-xs text-slate-400">
          <Image className="h-3.5 w-3.5" />
          <span>
            {data.screenshot_count} screenshot
            {data.screenshot_count !== 1 ? "s" : ""}
          </span>
        </div>
      )}

      {data.tailored_resume_excerpt && (
        <div>
          <div className="mb-1 flex items-center gap-1.5 text-xs text-slate-400">
            <FileText className="h-3.5 w-3.5" />
            <span>Tailored Resume</span>
          </div>
          <p className="line-clamp-4 text-xs leading-relaxed text-slate-300">
            {data.tailored_resume_excerpt}
          </p>
        </div>
      )}

      {data.id && (
        <Link
          to={`/apply?app=${data.id}`}
          className="mt-2 inline-block text-xs font-medium text-indigo-400 hover:text-indigo-300"
        >
          Review &rarr;
        </Link>
      )}
    </div>
  );
}

function ProfilePanel({ data }) {
  if (!data) return null;

  const percent = data.completion_percent ?? 0;
  const missing = data.missing_fields ?? [];

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-slate-200">
        Profile Completion
      </h3>

      <CompletionRing percent={percent} />

      {missing.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs font-medium text-slate-400">
            Missing Fields
          </p>
          <ul className="space-y-1">
            {missing.map((field) => (
              <li
                key={field}
                className="flex items-center gap-1.5 text-xs text-slate-300"
              >
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500" />
                {field}
              </li>
            ))}
          </ul>
        </div>
      )}

      {missing.length === 0 && (
        <p className="text-center text-xs text-emerald-400">
          All fields complete!
        </p>
      )}
    </div>
  );
}

function DefaultPanel({ data }) {
  return (
    <div className="space-y-4">
      {data?.name && (
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-indigo-600 text-sm font-semibold text-white">
            {data.name.charAt(0).toUpperCase()}
          </div>
          <div>
            <p className="text-sm font-medium text-slate-200">{data.name}</p>
            {data.target_role && (
              <p className="text-xs text-slate-400">{data.target_role}</p>
            )}
          </div>
        </div>
      )}

      <div className="rounded-lg border border-slate-700 bg-slate-800 p-3">
        <div className="flex items-center gap-2 text-slate-400">
          <Search className="h-4 w-4" />
          <p className="text-xs">Start by searching for jobs</p>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main ContextPanel                                                  */
/* ------------------------------------------------------------------ */

export default function ContextPanel({
  contextType,
  contextData,
  collapsed,
  onToggle,
}) {
  return (
    <div className="relative flex">
      {/* toggle button */}
      <button
        onClick={onToggle}
        aria-label={collapsed ? "Expand context panel" : "Collapse context panel"}
        className={clsx(
          "absolute -left-3 top-4 z-10 flex h-6 w-6 items-center justify-center",
          "rounded-full border border-slate-700 bg-slate-800 text-slate-400",
          "transition-colors hover:bg-slate-700 hover:text-slate-200"
        )}
      >
        {collapsed ? (
          <ChevronLeft className="h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" />
        )}
      </button>

      {/* panel */}
      {!collapsed && (
        <aside className="w-80 shrink-0 overflow-y-auto border-l border-slate-700 bg-slate-800/50 p-4">
          <div className="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-slate-500">
            {contextType === "job" && (
              <>
                <Building2 className="h-3.5 w-3.5" /> Job Details
              </>
            )}
            {contextType === "apply" && (
              <>
                <FileText className="h-3.5 w-3.5" /> Application
              </>
            )}
            {contextType === "profile" && (
              <>
                <User className="h-3.5 w-3.5" /> Profile
              </>
            )}
            {!contextType && (
              <>
                <User className="h-3.5 w-3.5" /> Overview
              </>
            )}
          </div>

          {contextType === "job" && <JobPanel data={contextData} />}
          {contextType === "apply" && <ApplyPanel data={contextData} />}
          {contextType === "profile" && <ProfilePanel data={contextData} />}
          {!contextType && <DefaultPanel data={contextData} />}
        </aside>
      )}
    </div>
  );
}
