import { useState, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Upload, FileText, CheckCircle, ArrowRight } from "lucide-react";
import clsx from "clsx";
import { useUploadResume, useUpdateProfile } from "../../hooks/useProfile";
import { useToast } from "../../hooks/useToast";
import LoadingSpinner from "../shared/LoadingSpinner";
import ToastContainer from "../shared/ToastContainer";

const ACCEPTED_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
];

const QUESTIONS = [
  {
    id: "target_role",
    question: "What type of role are you looking for?",
    placeholder: "e.g. Frontend Engineer, Data Scientist, Product Manager",
  },
  {
    id: "location",
    question: "Do you have a location preference?",
    placeholder: "e.g. San Francisco, New York, or anywhere",
  },
  {
    id: "work_style",
    question: "What is your preferred work style?",
    placeholder: "e.g. Remote, Hybrid, On-site",
  },
  {
    id: "salary_range",
    question: "What is your target salary range?",
    placeholder: "e.g. $120k-$160k, negotiable",
  },
];

/* ------------------------------------------------------------------ */
/*  Step 1 -- Upload Resume                                            */
/* ------------------------------------------------------------------ */

function StepUpload({ onComplete }) {
  const fileInputRef = useRef(null);
  const [dragOver, setDragOver] = useState(false);
  const uploadResume = useUploadResume();

  const handleFile = useCallback(
    (file) => {
      if (!file) return;
      if (!ACCEPTED_TYPES.includes(file.type)) {
        return;
      }
      uploadResume.mutate(file, {
        onSuccess: (data) => onComplete(data),
      });
    },
    [uploadResume, onComplete]
  );

  const onDrop = useCallback(
    (e) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files?.[0];
      handleFile(file);
    },
    [handleFile]
  );

  const onDragOver = useCallback((e) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const onDragLeave = useCallback(() => setDragOver(false), []);

  const onBrowse = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const onFileChange = useCallback(
    (e) => {
      const file = e.target.files?.[0];
      handleFile(file);
    },
    [handleFile]
  );

  if (uploadResume.isPending) {
    return (
      <div className="flex flex-col items-center gap-4 py-16">
        <LoadingSpinner label="Parsing your resume..." />
      </div>
    );
  }

  if (uploadResume.isError) {
    return (
      <div className="flex flex-col items-center gap-4 py-12">
        <p className="text-rose-400">
          Something went wrong uploading your resume. Please try again.
        </p>
        <button
          onClick={() => uploadResume.reset()}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    <>
      <div
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        className={clsx(
          "flex flex-col items-center gap-4 rounded-xl border-2 border-dashed px-8 py-16 transition-colors",
          dragOver
            ? "border-indigo-500 bg-indigo-500/10"
            : "border-slate-600 hover:border-indigo-500"
        )}
      >
        <Upload className="h-10 w-10 text-slate-500" />
        <p className="text-center text-slate-400">
          Drop your resume here (PDF or DOCX)
        </p>
        <button
          onClick={onBrowse}
          className="text-sm font-medium text-indigo-400 hover:text-indigo-300"
        >
          or browse files
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx"
          className="hidden"
          onChange={onFileChange}
        />
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Step 2 -- Profile Preview                                          */
/* ------------------------------------------------------------------ */

function StepPreview({ profileData, onConfirm }) {
  const name = profileData?.name || "Unknown";
  const email = profileData?.email || "";
  const skills = profileData?.skills || [];
  const education = profileData?.education || [];
  const experience = profileData?.experience || [];

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold text-slate-100">{name}</h3>
        {email && <p className="text-sm text-slate-400">{email}</p>}
      </div>

      {/* Skills */}
      {skills.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
            Skills
          </p>
          <div className="flex flex-wrap gap-2">
            {skills.map((skill, i) => (
              <span
                key={i}
                className="rounded-full bg-indigo-500/20 px-3 py-1 text-xs font-medium text-indigo-300"
              >
                {skill}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Education */}
      {education.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
            Education
          </p>
          <ul className="space-y-2">
            {education.map((entry, i) => (
              <li key={i} className="text-sm text-slate-300">
                {typeof entry === "string" ? entry : (
                  <>
                    <span className="font-medium text-slate-200">
                      {entry.degree || entry.title}
                    </span>
                    {entry.institution && (
                      <span className="text-slate-400">
                        {" "}at {entry.institution}
                      </span>
                    )}
                    {entry.year && (
                      <span className="text-slate-500"> ({entry.year})</span>
                    )}
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Experience */}
      {experience.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
            Experience
          </p>
          <ul className="space-y-2">
            {experience.map((entry, i) => (
              <li key={i} className="text-sm text-slate-300">
                {typeof entry === "string" ? entry : (
                  <>
                    <span className="font-medium text-slate-200">
                      {entry.title || entry.role}
                    </span>
                    {entry.company && (
                      <span className="text-slate-400">
                        {" "}at {entry.company}
                      </span>
                    )}
                    {entry.duration && (
                      <span className="text-slate-500">
                        {" "}({entry.duration})
                      </span>
                    )}
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="pt-2 text-sm text-slate-400">Does this look right?</p>

      <div className="flex gap-3">
        <button
          onClick={() => onConfirm(false)}
          className="rounded-lg border border-slate-600 px-5 py-2.5 text-sm font-medium text-slate-300 transition-colors hover:border-slate-500 hover:text-slate-100"
        >
          Let me fix it
        </button>
        <button
          onClick={() => onConfirm(true)}
          className="flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
        >
          Looks good
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Step 3 -- Quick Questions                                          */
/* ------------------------------------------------------------------ */

function StepQuestions({ onComplete, isSubmitting }) {
  const [currentIdx, setCurrentIdx] = useState(0);
  const [answers, setAnswers] = useState({});
  const [inputValue, setInputValue] = useState("");

  const currentQuestion = QUESTIONS[currentIdx];
  const isLast = currentIdx === QUESTIONS.length - 1;

  const handleNext = useCallback(() => {
    const updated = { ...answers, [currentQuestion.id]: inputValue.trim() };
    setAnswers(updated);
    setInputValue("");

    if (isLast) {
      onComplete(updated);
    } else {
      setCurrentIdx((i) => i + 1);
    }
  }, [answers, currentQuestion, inputValue, isLast, onComplete]);

  const handleKeyDown = useCallback(
    (e) => {
      if (e.key === "Enter" && inputValue.trim()) {
        handleNext();
      }
    },
    [handleNext, inputValue]
  );

  return (
    <div className="space-y-6">
      {/* Progress dots */}
      <div className="flex items-center gap-2">
        {QUESTIONS.map((_, i) => (
          <span
            key={i}
            className={clsx(
              "h-2 w-2 rounded-full transition-colors",
              i < currentIdx
                ? "bg-indigo-500"
                : i === currentIdx
                  ? "bg-indigo-400"
                  : "bg-slate-600"
            )}
          />
        ))}
      </div>

      {/* Answered questions (chat-like) */}
      <div className="space-y-4">
        {QUESTIONS.slice(0, currentIdx).map((q) => (
          <div key={q.id} className="space-y-1">
            <p className="text-sm text-slate-500">{q.question}</p>
            <div className="flex items-center gap-2">
              <CheckCircle className="h-4 w-4 shrink-0 text-emerald-500" />
              <p className="text-sm text-slate-200">{answers[q.id]}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Current question */}
      <div className="space-y-3">
        <p className="text-base font-medium text-slate-200">
          {currentQuestion.question}
        </p>
        <input
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={currentQuestion.placeholder}
          autoFocus
          className="w-full rounded-lg border border-slate-600 bg-slate-700/50 px-4 py-2.5 text-sm text-slate-100 placeholder-slate-500 outline-none transition-colors focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
        />
        <button
          onClick={handleNext}
          disabled={!inputValue.trim() || isSubmitting}
          className={clsx(
            "flex items-center gap-2 rounded-lg px-5 py-2.5 text-sm font-medium text-white transition-colors",
            !inputValue.trim() || isSubmitting
              ? "cursor-not-allowed bg-slate-600"
              : "bg-indigo-600 hover:bg-indigo-500"
          )}
        >
          {isSubmitting ? (
            <>
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              Saving...
            </>
          ) : isLast ? (
            <>
              Complete
              <CheckCircle className="h-4 w-4" />
            </>
          ) : (
            <>
              Next
              <ArrowRight className="h-4 w-4" />
            </>
          )}
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Step Indicator                                                     */
/* ------------------------------------------------------------------ */

function StepIndicator({ step }) {
  const steps = ["Upload Resume", "Review Profile", "Quick Questions"];
  return (
    <div className="mb-8 flex items-center justify-center gap-3">
      {steps.map((label, i) => {
        const stepNum = i + 1;
        const isCurrent = stepNum === step;
        const isCompleted = stepNum < step;
        return (
          <div key={label} className="flex items-center gap-3">
            {i > 0 && (
              <div
                className={clsx(
                  "h-px w-8",
                  isCompleted ? "bg-indigo-500" : "bg-slate-700"
                )}
              />
            )}
            <div className="flex items-center gap-2">
              <span
                className={clsx(
                  "flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold",
                  isCompleted
                    ? "bg-indigo-500 text-white"
                    : isCurrent
                      ? "border-2 border-indigo-500 text-indigo-400"
                      : "border border-slate-600 text-slate-500"
                )}
              >
                {isCompleted ? (
                  <CheckCircle className="h-4 w-4" />
                ) : (
                  stepNum
                )}
              </span>
              <span
                className={clsx(
                  "hidden text-xs font-medium sm:inline",
                  isCurrent ? "text-slate-200" : "text-slate-500"
                )}
              >
                {label}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main OnboardView                                                   */
/* ------------------------------------------------------------------ */

export default function OnboardView() {
  const navigate = useNavigate();
  const { addToast } = useToast();
  const updateProfile = useUpdateProfile();

  const [step, setStep] = useState(1);
  const [profileData, setProfileData] = useState(null);
  const [needsFix, setNeedsFix] = useState(false);

  const handleUploadComplete = useCallback((data) => {
    setProfileData(data);
    setStep(2);
  }, []);

  const handlePreviewConfirm = useCallback((looksGood) => {
    if (!looksGood) {
      setNeedsFix(true);
    }
    setStep(3);
  }, []);

  const handleQuestionsComplete = useCallback(
    (answers) => {
      const payload = { ...answers };
      if (needsFix) {
        payload._needs_review = true;
      }

      updateProfile.mutate(payload, {
        onSuccess: () => {
          addToast({
            message: "Profile set up successfully! Let's find you some jobs.",
            type: "success",
          });
          navigate("/discover", { replace: true });
        },
        onError: () => {
          addToast({
            message: "Failed to save your preferences. Please try again.",
            type: "error",
          });
        },
      });
    },
    [updateProfile, addToast, navigate, needsFix]
  );

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-900 p-4">
      <ToastContainer />

      <div className="w-full max-w-lg">
        <StepIndicator step={step} />

        <div className="rounded-2xl bg-slate-800 p-8 shadow-xl">
          {/* Header */}
          <div className="mb-6 flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-500/20">
              <FileText className="h-5 w-5 text-indigo-400" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-slate-100">
                {step === 1 && "Welcome to Job Finder"}
                {step === 2 && "Your Profile"}
                {step === 3 && "A Few Quick Questions"}
              </h1>
              <p className="text-sm text-slate-400">
                {step === 1 && "Upload your resume to get started"}
                {step === 2 && "Here's what we extracted"}
                {step === 3 && "Help us personalize your experience"}
              </p>
            </div>
          </div>

          {/* Step content */}
          {step === 1 && <StepUpload onComplete={handleUploadComplete} />}
          {step === 2 && (
            <StepPreview
              profileData={profileData}
              onConfirm={handlePreviewConfirm}
            />
          )}
          {step === 3 && (
            <StepQuestions
              onComplete={handleQuestionsComplete}
              isSubmitting={updateProfile.isPending}
            />
          )}
        </div>
      </div>
    </div>
  );
}
