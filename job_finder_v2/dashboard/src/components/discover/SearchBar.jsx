import { useState, useCallback } from "react";
import { Search, Loader2 } from "lucide-react";
import clsx from "clsx";

const SOURCES = ["All", "LinkedIn", "Greenhouse", "Lever"];

export default function SearchBar({ onSearch, isLoading = false }) {
  const [query, setQuery] = useState("");
  const [location, setLocation] = useState("");
  const [source, setSource] = useState("All");
  const [minFitScore, setMinFitScore] = useState(0);
  const [remoteOnly, setRemoteOnly] = useState(false);

  const handleSubmit = useCallback(
    (e) => {
      if (e) e.preventDefault();
      if (!query.trim() || isLoading) return;
      onSearch?.({
        query: query.trim(),
        location: location.trim() || undefined,
        source: source === "All" ? undefined : source.toLowerCase(),
        min_fit_score: minFitScore > 0 ? minFitScore : undefined,
        remote_only: remoteOnly || undefined,
      });
    },
    [query, location, source, minFitScore, remoteOnly, onSearch, isLoading]
  );

  const handleKeyDown = useCallback(
    (e) => {
      if (e.key === "Enter") handleSubmit();
    },
    [handleSubmit]
  );

  return (
    <div className="rounded-xl bg-slate-800 p-4">
      {/* Main search row */}
      <form onSubmit={handleSubmit} className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Python backend jobs..."
            className="w-full rounded-lg border border-slate-600 bg-slate-900 py-2.5 pl-10 pr-3 text-sm text-slate-100 placeholder-slate-500 transition-colors focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </div>

        <div className="relative w-48">
          <input
            type="text"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Boston, MA"
            className="w-full rounded-lg border border-slate-600 bg-slate-900 py-2.5 px-3 text-sm text-slate-100 placeholder-slate-500 transition-colors focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </div>

        <button
          type="submit"
          disabled={!query.trim() || isLoading}
          className={clsx(
            "flex items-center gap-2 rounded-lg px-5 py-2.5 text-sm font-medium text-white transition-colors",
            !query.trim() || isLoading
              ? "cursor-not-allowed bg-indigo-500/50"
              : "bg-indigo-500 hover:bg-indigo-600 active:bg-indigo-700"
          )}
        >
          {isLoading ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Searching...
            </>
          ) : (
            "Find"
          )}
        </button>
      </form>

      {/* Filter chips row */}
      <div className="mt-3 flex flex-wrap items-center gap-4">
        {/* Source toggle group */}
        <div className="flex items-center gap-1 rounded-lg bg-slate-900 p-0.5">
          {SOURCES.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSource(s)}
              className={clsx(
                "rounded-md px-3 py-1 text-xs font-medium transition-colors",
                source === s
                  ? "bg-indigo-500 text-white"
                  : "text-slate-400 hover:text-slate-200"
              )}
            >
              {s}
            </button>
          ))}
        </div>

        {/* Min fit score slider */}
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-400">Min Fit</label>
          <input
            type="range"
            min={0}
            max={100}
            value={minFitScore}
            onChange={(e) => setMinFitScore(Number(e.target.value))}
            className="h-1.5 w-24 cursor-pointer appearance-none rounded-full bg-slate-700 accent-indigo-500"
          />
          <span className="min-w-[2rem] text-xs font-medium text-slate-300">
            {minFitScore}
          </span>
        </div>

        {/* Remote only toggle */}
        <button
          type="button"
          onClick={() => setRemoteOnly((prev) => !prev)}
          className={clsx(
            "flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors",
            remoteOnly
              ? "border-indigo-500 bg-indigo-500/20 text-indigo-400"
              : "border-slate-600 text-slate-400 hover:border-slate-500 hover:text-slate-300"
          )}
        >
          <span
            className={clsx(
              "inline-block h-2 w-2 rounded-full",
              remoteOnly ? "bg-indigo-400" : "bg-slate-500"
            )}
          />
          Remote only
        </button>
      </div>
    </div>
  );
}
