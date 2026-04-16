import clsx from "clsx";
import { X } from "lucide-react";

const BORDER_COLOR = {
  success: "border-l-emerald-500",
  error: "border-l-rose-500",
  info: "border-l-indigo-500",
};

export default function Toast({ message, type = "info", onClose }) {
  return (
    <div
      className={clsx(
        "pointer-events-auto flex w-80 items-start gap-3 rounded-lg border-l-4 bg-slate-800 p-4 shadow-lg",
        "animate-slide-in-right",
        BORDER_COLOR[type] || BORDER_COLOR.info
      )}
      role="alert"
    >
      <p className="flex-1 text-sm text-slate-200">{message}</p>
      <button
        onClick={onClose}
        className="shrink-0 text-slate-400 transition-colors hover:text-slate-200"
        aria-label="Close notification"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
