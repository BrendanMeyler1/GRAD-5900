export default function SkeletonCard() {
  return (
    <div className="animate-pulse rounded-xl bg-slate-800 p-5">
      <div className="mb-4 flex items-center justify-between">
        <div className="h-5 w-3/5 rounded bg-slate-700" />
        <div className="h-5 w-16 rounded-full bg-slate-700" />
      </div>
      <div className="space-y-2.5">
        <div className="h-3.5 w-full rounded bg-slate-700" />
        <div className="h-3.5 w-4/5 rounded bg-slate-700" />
        <div className="h-3.5 w-2/3 rounded bg-slate-700" />
      </div>
      <div className="mt-4 flex gap-2">
        <div className="h-6 w-20 rounded-full bg-slate-700" />
        <div className="h-6 w-16 rounded-full bg-slate-700" />
      </div>
    </div>
  );
}
