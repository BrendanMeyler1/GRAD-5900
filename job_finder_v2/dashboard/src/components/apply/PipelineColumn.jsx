export default function PipelineColumn({ title, count, children }) {
  return (
    <div className="flex min-w-[260px] flex-1 flex-col rounded-xl bg-slate-900/50 border border-slate-700/40">
      {/* Column header */}
      <div className="flex items-center gap-2 border-b border-slate-700/40 px-4 py-3">
        <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
        <span className="inline-flex items-center justify-center rounded-full bg-slate-700 px-2 py-0.5 text-xs font-medium text-slate-300">
          {count ?? 0}
        </span>
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
