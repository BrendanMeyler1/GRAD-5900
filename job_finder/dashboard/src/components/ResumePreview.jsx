function getPreviewText(value) {
  const text = String(value || "").trim();
  if (!text) {
    return "Not generated yet for this application.";
  }
  return text;
}

export default function ResumePreview({ applicationDetail }) {
  const state = applicationDetail?.state ?? {};
  const resume =
    state.tailored_resume_final ||
    state.tailored_resume_tokenized ||
    "";
  const coverLetter =
    state.cover_letter_final ||
    state.cover_letter_tokenized ||
    "";

  return (
    <section className="card">
      <h2>Tailored Docs Preview</h2>
      <p className="card-subtitle">
        Review generated artifacts before any live submission.
      </p>
      <div className="grid-2">
        <div>
          <p className="list-title">Resume</p>
          <pre className="preview-pane">{getPreviewText(resume)}</pre>
        </div>
        <div>
          <p className="list-title">Cover Letter</p>
          <pre className="preview-pane">{getPreviewText(coverLetter)}</pre>
        </div>
      </div>
    </section>
  );
}
