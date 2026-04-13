function statusClass(status) {
  return `status-pill status-${String(status || "").toLowerCase()}`;
}

const STAGES = [
  { key: "QUEUED", label: "Queued" },
  { key: "IN_PROGRESS", label: "Filling" },
  { key: "SUBMITTED", label: "Submitted" },
  { key: "INTERVIEW_SCHEDULED", label: "Interview" },
  { key: "OFFER", label: "Offer" },
];

function ProgressStepper({ currentStatus }) {
  const statusUpper = String(currentStatus || "UNKNOWN").toUpperCase();
  
  // Find current index; handle aliases
  let currentIndex = -1;
  const mappedStatus = 
    statusUpper.includes("LIVE") || statusUpper.includes("SHADOW") ? "IN_PROGRESS" :
    statusUpper === "REJECTED" ? "SUBMITTED" : // Still show it progressed to submission
    statusUpper;

  if (statusUpper === "FAILED" || statusUpper === "ESCALATED") {
    return <div className="stepper-error">⚠️ {statusUpper}</div>;
  }

  currentIndex = STAGES.findIndex(s => s.key === mappedStatus);

  return (
    <div style={{ display: "flex", gap: "4px", marginTop: "8px", alignItems: "center" }}>
      {STAGES.map((stage, idx) => {
        const isActive = idx === currentIndex || (idx === 2 && currentIndex > 2);
        const isPast = idx < currentIndex;
        
        let color = "#cbd5e1"; // default gray
        if (isActive) color = "#3b82f6"; // blue
        if (isPast) color = "#10b981"; // green
        
        // Special colors
        if (isActive && stage.key === "INTERVIEW_SCHEDULED") color = "#8b5cf6";
        if (isActive && stage.key === "OFFER") color = "#f59e0b";

        if (statusUpper === "REJECTED") {
            if (isActive) color = "#ef4444";
            if (idx > currentIndex) color = "#f87171"; // lighter red
        }

        return (
          <div key={stage.key} style={{ display: "flex", alignItems: "center", flex: 1 }}>
            <div 
              style={{ 
                height: "6px", 
                flex: 1, 
                backgroundColor: color, 
                borderRadius: "4px",
                transition: "background-color 0.3s ease" 
              }} 
              title={stage.label}
            />
          </div>
        );
      })}
    </div>
  );
}

export default function ApplicationTracker({
  applications,
  selectedAppId,
  onSelectApp,
  onRefresh,
  loading,
  onApproveLive,
  onResume,
  onAbort,
  actionBusy
}) {
  const selected = applications.find((app) => String(app.application_id) === String(selectedAppId));
  return (
    <section className="card">
      <h2>Application Tracker</h2>
      <p className="card-subtitle">
        Lifecycle view from queued to submitted and final outcomes.
      </p>
      <div className="toolbar">
        <button className="btn" onClick={onRefresh} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh Tracker"}
        </button>
        <button
          className="btn btn-primary"
          onClick={() => selected && onApproveLive(selected.application_id)}
          disabled={!selected || actionBusy}
        >
          {actionBusy ? "Working..." : "Approve + Submit Live"}
        </button>
        <button
          className="btn"
          onClick={() => selected && onResume(selected.application_id)}
          disabled={!selected || actionBusy}
        >
          Resume
        </button>
        <button
          className="btn btn-danger"
          onClick={() => selected && onAbort(selected.application_id)}
          disabled={!selected || actionBusy}
        >
          Abort
        </button>
      </div>
      <div className="list">
        {applications.length === 0 ? (
          <div className="message">No applications yet. Start one from the decision queue.</div>
        ) : null}
        {applications.map((app) => {
          const appId = String(app.application_id ?? "");
          return (
            <article
              key={appId}
              className={`list-item${selectedAppId === appId ? " selected" : ""}`}
            >
              <p className="list-title">{app.role_title || "Untitled role"}</p>
              <div className="list-meta">{app.company || "Unknown company"}</div>
              <div className="toolbar">
                <span className={statusClass(app.status)}>{app.status || "UNKNOWN"}</span>
                <button className="btn" onClick={() => onSelectApp(appId)}>
                  Open
                </button>
              </div>
              <ProgressStepper currentStatus={app.status} />
            </article>
          );
        })}
      </div>
    </section>
  );
}
