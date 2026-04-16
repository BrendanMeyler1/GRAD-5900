import { useState, useRef, useEffect } from "react";
import { Link } from "react-router-dom";
import { UploadCloud } from "lucide-react";
import { useProfile } from "../../hooks/useProfile";
import clsx from "clsx";

const REQUIRED_FIELDS = [
  { key: "name", label: "Name" },
  { key: "email", label: "Email" },
  { key: "phone", label: "Phone number" },
  { key: "experience", label: "Work experience" },
  { key: "education", label: "Education" },
  { key: "skills", label: "Skills" },
  { key: "target_role", label: "Target role" },
  { key: "location", label: "Location preference" },
];

function getCompletionData(profile) {
  if (!profile) return { percent: 0, missing: REQUIRED_FIELDS.map((f) => f.label) };

  const missing = [];
  for (const field of REQUIRED_FIELDS) {
    const value = profile[field.key];
    if (
      value === undefined ||
      value === null ||
      value === "" ||
      (Array.isArray(value) && value.length === 0)
    ) {
      missing.push(field.label);
    }
  }

  const filled = REQUIRED_FIELDS.length - missing.length;
  const percent = Math.round((filled / REQUIRED_FIELDS.length) * 100);

  return { percent, missing };
}

function getRingColor(percent) {
  if (percent >= 60) return "text-indigo-500";
  if (percent >= 30) return "text-amber-500";
  return "text-rose-500";
}

function getStrokeColor(percent) {
  if (percent >= 60) return "#6366f1";
  if (percent >= 30) return "#f59e0b";
  return "#f43f5e";
}

export default function ProfileCompletionBar() {
  const { profile, isLoading } = useProfile();
  const [open, setOpen] = useState(false);
  const popoverRef = useRef(null);
  const buttonRef = useRef(null);

  const { percent, missing } = getCompletionData(profile);

  useEffect(() => {
    function handleClickOutside(e) {
      if (
        popoverRef.current &&
        !popoverRef.current.contains(e.target) &&
        buttonRef.current &&
        !buttonRef.current.contains(e.target)
      ) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  if (isLoading) return null;

  const radius = 16;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (percent / 100) * circumference;

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        onClick={() => setOpen((prev) => !prev)}
        className="group flex items-center gap-2 rounded-lg p-1 transition-colors hover:bg-slate-800"
        aria-label={`Profile ${percent}% complete`}
      >
        <svg className="h-10 w-10" viewBox="0 0 40 40">
          <circle
            cx="20"
            cy="20"
            r={radius}
            fill="none"
            stroke="#334155"
            strokeWidth="3"
          />
          <circle
            cx="20"
            cy="20"
            r={radius}
            fill="none"
            stroke={getStrokeColor(percent)}
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            transform="rotate(-90 20 20)"
            className="transition-all duration-500"
          />
          <text
            x="20"
            y="20"
            textAnchor="middle"
            dominantBaseline="central"
            className={clsx("text-[9px] font-bold", getRingColor(percent))}
            fill="currentColor"
          >
            {percent}%
          </text>
        </svg>
      </button>

      {open && (
        <div
          ref={popoverRef}
          className="absolute bottom-full left-0 z-50 mb-2 w-56 rounded-lg border border-slate-700 bg-slate-800 p-3 shadow-xl"
        >
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
            Profile Completion
          </p>
          {missing.length === 0 ? (
            <p className="text-sm text-emerald-400">All fields complete!</p>
          ) : (
            <>
              <p className="mb-1.5 text-xs text-slate-500">
                Missing fields:
              </p>
              <ul className="space-y-1">
                {missing.map((field) => (
                  <li
                    key={field}
                    className="flex items-center gap-1.5 text-sm text-slate-300"
                  >
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-500" />
                    {field}
                  </li>
                ))}
              </ul>
            </>
          )}
          <Link
            to="/onboard"
            onClick={() => setOpen(false)}
            className="mt-3 flex items-center gap-1.5 text-xs font-medium text-indigo-400 hover:text-indigo-300"
          >
            <UploadCloud className="h-3.5 w-3.5" />
            Upload / replace resume
          </Link>
        </div>
      )}
    </div>
  );
}
