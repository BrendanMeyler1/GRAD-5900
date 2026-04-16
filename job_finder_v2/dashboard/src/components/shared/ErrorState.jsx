import { AlertCircle } from "lucide-react";

export default function ErrorState({ message, onRetry }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <AlertCircle className="mb-4 h-12 w-12 text-rose-500" />
      <p className="mb-4 max-w-sm text-sm text-slate-400">
        {message || "Something went wrong."}
      </p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="rounded-lg border border-slate-600 px-4 py-2 text-sm font-medium text-slate-300 transition-colors hover:border-slate-500 hover:text-slate-100"
        >
          Try again
        </button>
      )}
    </div>
  );
}
