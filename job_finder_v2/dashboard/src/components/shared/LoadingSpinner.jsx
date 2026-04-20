import clsx from "clsx";

const SIZES = {
  sm: "h-5 w-5",
  md: "h-8 w-8",
};

export default function LoadingSpinner({ size = "md", label }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2">
      <div
        className={clsx(
          "animate-spin rounded-full border-4 border-slate-700 border-t-indigo-500",
          SIZES[size] || SIZES.md
        )}
        role="status"
        aria-label={label || "Loading"}
      />
      {label && <p className="text-sm text-slate-400">{label}</p>}
    </div>
  );
}
