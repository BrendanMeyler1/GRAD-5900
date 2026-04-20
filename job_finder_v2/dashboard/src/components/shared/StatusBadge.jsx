import clsx from "clsx";

const STATUS_STYLES = {
  shadow_review: { bg: "bg-amber-500/20", text: "text-amber-500", label: "Shadow Review" },
  pending:       { bg: "bg-amber-500/20", text: "text-amber-500", label: "Pending" },
  submitted:     { bg: "bg-emerald-500/20", text: "text-emerald-500", label: "Submitted" },
  rejected:      { bg: "bg-rose-500/20", text: "text-rose-500", label: "Rejected" },
  offer_received:{ bg: "bg-indigo-500/20", text: "text-indigo-500", label: "Offer Received" },
  skipped:       { bg: "bg-slate-500/20", text: "text-slate-400", label: "Skipped" },
  failed:        { bg: "bg-slate-500/20", text: "text-slate-400", label: "Failed" },
};

const DEFAULT_STYLE = { bg: "bg-slate-500/20", text: "text-slate-400" };

export default function StatusBadge({ status }) {
  const style = STATUS_STYLES[status] || DEFAULT_STYLE;
  const label = style.label || status?.replace(/_/g, " ") || "Unknown";

  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize",
        style.bg,
        style.text
      )}
    >
      {label}
    </span>
  );
}
