import { useState } from "react";
import { Trash2 } from "lucide-react";

/**
 * A kanban column for the Apply view.
 *
 * Props:
 *   title     {string}   Column heading.
 *   count     {number}   Badge count shown next to the heading.
 *   onClear   {function} If provided, a "Clear all" action appears in the header.
 *                        Called with no arguments when the user confirms.
 *   children  {ReactNode}
 */
export default function PipelineColumn({ title, count, onClear, children }) {
  const [confirming, setConfirming] = useState(false);

  const handleClearClick = () => setConfirming(true);
  const handleConfirm = () => {
    setConfirming(false);
    onClear?.();
  };
  const handleCancel = () => setConfirming(false);

  return (
    <div className="flex min-w-[260px] flex-1 flex-col rounded-xl bg-slate-900/50 border border-slate-700/40">
      {/* Column header */}
      <div className="flex items-center gap-2 border-b border-slate-700/40 px-4 py-3">
        <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
        <span className="inline-flex items-center justify-center rounded-full bg-slate-700 px-2 py-0.5 text-xs font-medium text-slate-300">
          {count ?? 0}
        </span>

        {/* Clear all — only shown when the column is clearable and has items */}
        {onClear && (count ?? 0) > 0 && (
          <div className="ml-auto flex items-center gap-1">
            {confirming ? (
              <>
                <span className="text-xs text-slate-400">Clear {count}?</span>
                <button
                  type="button"
                  onClick={handleConfirm}
                  className="rounded px-1.5 py-0.5 text-xs font-medium text-rose-400 hover:bg-rose-500/10 transition-colors"
                >
                  Yes
                </button>
                <button
                  type="button"
                  onClick={handleCancel}
                  className="rounded px-1.5 py-0.5 text-xs font-medium text-slate-400 hover:text-slate-200 transition-colors"
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={handleClearClick}
                title="Clear all applications in this column"
                className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-slate-500 transition-colors hover:bg-rose-500/10 hover:text-rose-400"
              >
                <Trash2 className="h-3 w-3" />
                Clear all
              </button>
            )}
          </div>
        )}
      </div>

      {/* Scrollable card list */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {(!children || (Array.isArray(children) && children.length === 0)) ? (
          <p className="py-8 text-center text-xs text-slate-500">
            No applications here yet
          </p>
        ) : (
          children
        )}
      </div>
    </div>
  );
}
