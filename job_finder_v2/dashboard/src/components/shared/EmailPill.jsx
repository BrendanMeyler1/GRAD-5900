import clsx from "clsx";
import { Calendar, XCircle, Gift, MessageCircle } from "lucide-react";

const CATEGORY_MAP = {
  interview_request: {
    icon: Calendar,
    label: "Interview",
    bg: "bg-indigo-500/20",
    text: "text-indigo-500",
  },
  rejection: {
    icon: XCircle,
    label: "Rejected",
    bg: "bg-rose-500/20",
    text: "text-rose-500",
  },
  offer: {
    icon: Gift,
    label: "Offer",
    bg: "bg-emerald-500/20",
    text: "text-emerald-500",
  },
  followup_needed: {
    icon: MessageCircle,
    label: "Follow-up",
    bg: "bg-amber-500/20",
    text: "text-amber-500",
  },
};

const DEFAULT_CATEGORY = {
  icon: MessageCircle,
  label: "Email",
  bg: "bg-slate-500/20",
  text: "text-slate-400",
};

export default function EmailPill({ category, summary }) {
  const config = CATEGORY_MAP[category] || DEFAULT_CATEGORY;
  const Icon = config.icon;

  return (
    <span
      title={summary}
      className={clsx(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        config.bg,
        config.text
      )}
    >
      <Icon className="h-3 w-3" />
      {config.label}
    </span>
  );
}
