import { useState, useCallback, useMemo, useRef } from "react";
import {
  X,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  Camera,
  FileText,
  MessageSquare,
  AlertTriangle,
} from "lucide-react";
import DiffMatchPatch from "diff-match-patch";
import { useNavigate } from "react-router-dom";
import Lightbox from "../shared/Lightbox";
import { useUpdateApplication, useApproveApplication } from "../../hooks/useApplications";
import { useProfile } from "../../hooks/useProfile";
import { useToast } from "../../hooks/useToast";

const TABS = [
  { key: "screenshots", label: "Screenshots", icon: Camera },
  { key: "resume", label: "Resume Diff", icon: FileText },
  { key: "cover", label: "Cover Letter & Q&A", icon: MessageSquare },
];

/* ------------------------------------------------------------------ */
/*  Tab 1 - Screenshots                                               */
/* ------------------------------------------------------------------ */

function ScreenshotsTab({ application }) {
  const screenshots = application?.screenshots ?? [];
  const fillLog = application?.fill_log ?? [];
  const [idx, setIdx] = useState(0);
  const [lightboxOpen, setLightboxOpen] = useState(false);

  const goPrev = useCallback(
    () => setIdx((i) => (i > 0 ? i - 1 : screenshots.length - 1)),
    [screenshots.length]
  );
  const goNext = useCallback(
    () => setIdx((i) => (i < screenshots.length - 1 ? i + 1 : 0)),
    [screenshots.length]
  );

  if (screenshots.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-slate-500">
        No screenshots captured for this application.
      </p>
    );
  }

  return (
    <div className="flex flex-col items-center gap-4">
      {/* Fields-filled badge */}
      {fillLog.length > 0 && (
        <span className="rounded-full bg-indigo-500/20 px-3 py-1 text-xs font-medium text-indigo-400">
          {fillLog.length} field{fillLog.length !== 1 ? "s" : ""} filled
        </span>
      )}

      {/* Image area */}
      <div className="relative flex w-full items-center justify-center">
        {screenshots.length > 1 && (
          <button
            onClick={goPrev}
            className="absolute left-0 rounded-full p-1 text-slate-400 hover:bg-slate-700 hover:text-slate-200"
            aria-label="Previous screenshot"
          >
            <ChevronLeft className="h-6 w-6" />
          </button>
        )}

        <button
          onClick={() => setLightboxOpen(true)}
          className="focus:outline-none"
        >
          <img
            src={screenshots[idx]}
            alt={`Screenshot ${idx + 1} of ${screenshots.length}`}
            className="max-h-[40vh] rounded-lg border border-slate-700 object-contain"
          />
        </button>

        {screenshots.length > 1 && (
          <button
            onClick={goNext}
            className="absolute right-0 rounded-full p-1 text-slate-400 hover:bg-slate-700 hover:text-slate-200"
            aria-label="Next screenshot"
          >
            <ChevronRight className="h-6 w-6" />
          </button>
        )}
      </div>

      {/* Counter */}
      {screenshots.length > 1 && (
        <span className="text-xs text-slate-500">
          {idx + 1} / {screenshots.length}
        </span>
      )}

      {/* Lightbox */}
      {lightboxOpen && (
        <Lightbox
          images={screenshots}
          currentIndex={idx}
          onClose={() => setLightboxOpen(false)}
          onNext={goNext}
          onPrev={goPrev}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab 2 - Resume Diff                                                */
/* ------------------------------------------------------------------ */

function ResumeDiffTab({ application }) {
  const { profile } = useProfile();
  const original = profile?.resume_raw_text ?? "";
  const tailored = application?.tailored_resume_text ?? "";

  const diffParts = useMemo(() => {
    if (!original && !tailored) return [];
    const dmp = new DiffMatchPatch();
    const diffs = dmp.diff_main(original, tailored);
    dmp.diff_cleanupSemantic(diffs);
    return diffs;
  }, [original, tailored]);

  if (!original && !tailored) {
    return (
      <p className="py-12 text-center text-sm text-slate-500">
        No resume data available for comparison.
      </p>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-4">
      {/* Original */}
      <div className="flex flex-col gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Original
        </h4>
        <div className="max-h-[40vh] overflow-y-auto rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-500 whitespace-pre-wrap">
          {diffParts.map(([op, text], i) => {
            if (op === DiffMatchPatch.DIFF_INSERT) return null;
            if (op === DiffMatchPatch.DIFF_DELETE) {
              return (
                <span key={i} className="bg-rose-500/20 text-rose-400 line-through">
                  {text}
                </span>
              );
            }
            return <span key={i}>{text}</span>;
          })}
        </div>
      </div>

      {/* Tailored */}
      <div className="flex flex-col gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Tailored
        </h4>
        <div className="max-h-[40vh] overflow-y-auto rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-100 whitespace-pre-wrap">
          {diffParts.map(([op, text], i) => {
            if (op === DiffMatchPatch.DIFF_DELETE) return null;
            if (op === DiffMatchPatch.DIFF_INSERT) {
              return (
                <span key={i} className="bg-emerald-500/20 text-emerald-400">
                  {text}
                </span>
              );
            }
            return <span key={i}>{text}</span>;
          })}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab 3 - Cover Letter & Q&A                                        */
/* ------------------------------------------------------------------ */

function CoverLetterTab({ application }) {
  const updateMutation = useUpdateApplication();
  const coverLetterRef = useRef(null);
  const [expandedQ, setExpandedQ] = useState(new Set());
  const answerRefs = useRef({});
  const customQA = application?.custom_qa ?? [];

  const handleCoverLetterBlur = useCallback(() => {
    const value = coverLetterRef.current?.value;
    if (value == null || value === application?.cover_letter) return;
    updateMutation.mutate({
      appId: application.id,
      data: { cover_letter: value },
    });
  }, [application, updateMutation]);

  const toggleQ = useCallback((index) => {
    setExpandedQ((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }, []);

  const handleAnswerBlur = useCallback(
    (index) => {
      const value = answerRefs.current[index]?.value;
      if (value == null) return;
      const updated = [...customQA];
      updated[index] = { ...updated[index], answer: value };
      updateMutation.mutate({
        appId: application.id,
        data: { custom_qa: updated },
      });
    },
    [application, customQA, updateMutation]
  );

  return (
    <div className="flex flex-col gap-6">
      {/* Cover letter */}
      <div className="flex flex-col gap-2">
        <label className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Cover Letter
        </label>
        <textarea
          ref={coverLetterRef}
          defaultValue={application?.cover_letter ?? ""}
          onBlur={handleCoverLetterBlur}
          rows={8}
          className="w-full resize-y rounded-lg border border-slate-700 bg-slate-900 p-3 text-sm text-slate-200 placeholder-slate-600 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          placeholder="No cover letter generated yet."
        />
      </div>

      {/* Custom Q&A accordion */}
      {customQA.length > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Custom Questions & Answers
          </h4>
          <div className="flex flex-col gap-1">
            {customQA.map((qa, i) => {
              const isOpen = expandedQ.has(i);
              return (
                <div
                  key={i}
                  className="rounded-lg border border-slate-700 bg-slate-900"
                >
                  <button
                    type="button"
                    onClick={() => toggleQ(i)}
                    className="flex w-full items-center justify-between px-3 py-2 text-left text-sm text-slate-200 hover:bg-slate-800/50"
                  >
                    <span className="truncate pr-2">
                      {qa.question || `Question ${i + 1}`}
                    </span>
                    {isOpen ? (
                      <ChevronUp className="h-4 w-4 shrink-0 text-slate-500" />
                    ) : (
                      <ChevronDown className="h-4 w-4 shrink-0 text-slate-500" />
                    )}
                  </button>
                  {isOpen && (
                    <div className="border-t border-slate-700 p-3">
                      <textarea
                        ref={(el) => {
                          answerRefs.current[i] = el;
                        }}
                        defaultValue={qa.answer ?? ""}
                        onBlur={() => handleAnswerBlur(i)}
                        rows={3}
                        className="w-full resize-y rounded-md border border-slate-700 bg-slate-800 p-2 text-sm text-slate-200 placeholder-slate-600 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                        placeholder="No answer yet."
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ReviewPanel (main export)                                          */
/* ------------------------------------------------------------------ */

export default function ReviewPanel({ application, onClose }) {
  const [activeTab, setActiveTab] = useState("screenshots");
  const [confirmingApprove, setConfirmingApprove] = useState(false);
  const approveMutation = useApproveApplication();
  const { addToast } = useToast();
  const navigate = useNavigate();

  if (!application) return null;

  const handleApprove = () => {
    approveMutation.mutate(application.id, {
      onSuccess: () => {
        addToast({ message: "Application submitted!", type: "success" });
        setConfirmingApprove(false);
        onClose?.();
      },
      onError: (err) => {
        addToast({
          message: err?.message || "Failed to submit application.",
          type: "error",
        });
        setConfirmingApprove(false);
      },
    });
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-30 bg-black/40"
        onClick={onClose}
      />

      {/* Drawer */}
      <div className="fixed inset-x-0 bottom-0 z-40 flex h-[70vh] flex-col rounded-t-xl bg-slate-800 shadow-2xl">
        {/* Header bar */}
        <div className="flex items-start justify-between border-b border-slate-700 px-6 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-lg font-bold text-slate-100">
              {application.company}
              <span className="mx-2 text-slate-600">&mdash;</span>
              <span className="font-normal text-slate-300">
                {application.job_title}
              </span>
            </h2>
          </div>
          <button
            onClick={onClose}
            className="ml-4 shrink-0 rounded-full p-1.5 text-slate-400 transition-colors hover:bg-slate-700 hover:text-slate-200"
            aria-label="Close panel"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-slate-700 px-6">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                activeTab === key
                  ? "border-indigo-500 text-indigo-400"
                  : "border-transparent text-slate-400 hover:text-slate-200"
              }`}
            >
              <Icon className="h-4 w-4" />
              {label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {activeTab === "screenshots" && (
            <ScreenshotsTab application={application} />
          )}
          {activeTab === "resume" && (
            <ResumeDiffTab application={application} />
          )}
          {activeTab === "cover" && (
            <CoverLetterTab application={application} />
          )}
        </div>

        {/* Bottom action bar */}
        <div className="flex items-center justify-between border-t border-slate-700 px-6 py-3">
          <div className="flex items-center gap-3">
            {/* Abort */}
            <button
              onClick={() => onClose?.()}
              className="rounded-lg border border-rose-500/50 px-4 py-2 text-sm font-medium text-rose-400 transition-colors hover:bg-rose-500/10"
            >
              Abort
            </button>

            {/* Edit in Chat */}
            <button
              onClick={() => navigate(`/chat?app=${application.id}`)}
              className="rounded-lg border border-indigo-500/50 px-4 py-2 text-sm font-medium text-indigo-400 transition-colors hover:bg-indigo-500/10"
            >
              Edit in Chat
            </button>
          </div>

          {/* Approve & Submit area */}
          <div className="flex items-center gap-3">
            {confirmingApprove ? (
              <>
                <span className="flex items-center gap-1.5 text-sm text-amber-400">
                  <AlertTriangle className="h-4 w-4" />
                  Are you sure? This will submit the real application.
                </span>
                <button
                  onClick={() => setConfirmingApprove(false)}
                  className="rounded-lg px-3 py-2 text-sm font-medium text-slate-400 transition-colors hover:text-slate-200"
                >
                  Cancel
                </button>
                <button
                  onClick={handleApprove}
                  disabled={approveMutation.isPending}
                  className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-50"
                >
                  {approveMutation.isPending ? "Submitting..." : "Yes, submit"}
                </button>
              </>
            ) : (
              <button
                onClick={() => setConfirmingApprove(true)}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500"
              >
                Approve & Submit
              </button>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
