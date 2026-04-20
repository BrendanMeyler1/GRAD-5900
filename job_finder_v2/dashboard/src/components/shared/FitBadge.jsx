import clsx from "clsx";

export default function FitBadge({ score }) {
  const isNull = score === null || score === undefined;

  const color = isNull
    ? "slate"
    : score >= 70
      ? "emerald"
      : score >= 40
        ? "amber"
        : "rose";

  return (
    <span
      className={clsx(
        "inline-flex items-center justify-center rounded-full px-2.5 py-0.5 text-xs font-semibold",
        {
          "bg-emerald-500/20 text-emerald-500": color === "emerald",
          "bg-amber-500/20 text-amber-500": color === "amber",
          "bg-rose-500/20 text-rose-500": color === "rose",
          "bg-slate-500/20 text-slate-400": color === "slate",
        }
      )}
    >
      {isNull ? "\u2014" : score}
    </span>
  );
}
